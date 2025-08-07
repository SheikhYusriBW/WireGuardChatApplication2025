# client_logic.py
import asyncio
import socket
import msgpack
import random
import sys
import time
import struct
from html import escape
import traceback # For printing stack traces

# --- Import your crypto functions and constants ---
try:
    from wg_crypto import (
        DH_Generate, DH, AEAD_encrypt, AEAD_decrypt, Hash, MixHash, Mac,
        Kdf1, Kdf2, Kdf3, Timestamp,
        CONSTRUCTION, IDENTIFIER, LABEL_MAC1,
        SERVER_STATIC_PUBLIC_KEY_BYTES,
        YOUR_STATIC_PRIVATE_KEY_BYTES,
        YOUR_STATIC_PUBLIC_KEY_BYTES
    )
except ImportError:
    print("FATAL ERROR: wg_crypto.py not found or contains errors. Please create and implement it.")
    sys.exit(1)
except Exception as e:
    print(f"FATAL ERROR: Error importing from wg_crypto.py: {e}")
    traceback.print_exc()
    sys.exit(1)

# --- Configuration ---
SERVER_HOST = "csc4026z.link"
SERVER_PORT = 51820
PING_INTERVAL = 25

# --- Chat Protocol Message Types ---
# Requests
CONNECT = 1
PING = 3
CHANNEL_CREATE = 4
CHANNEL_LIST = 5
CHANNEL_INFO = 6
CHANNEL_JOIN = 7
CHANNEL_LEAVE = 8
CHANNEL_MESSAGE = 9
WHOIS = 10
WHOAMI = 11
USER_MESSAGE = 12
SET_USERNAME = 13
USER_LIST = 14
DISCONNECT = 23

# Responses / Server-initiated
ERROR = 20
OK = 21
CONNECT_RESPONSE = 22
PING_RESPONSE = 24
CHANNEL_CREATE_RESPONSE = 25
CHANNEL_LIST_RESPONSE = 26
CHANNEL_INFO_RESPONSE = 27
CHANNEL_JOIN_RESPONSE = 28
CHANNEL_LEFT_RESPONSE = 29
CHANNEL_MESSAGE_RESPONSE = 30 
WHOIS_RESPONSE = 31
WHOAMI_RESPONSE = 32
USER_MESSAGE_RESPONSE = 33 
SET_USERNAME_RESPONSE = 34
USER_LIST_RESPONSE = 35
SERVER_MESSAGE = 36 
SERVER_SHUTDOWN = 37

# --- Wireguard Message Types ---
WG_MSG_TYPE_INITIATION = 0x1
WG_MSG_TYPE_RESPONSE = 0x2
WG_MSG_TYPE_TRANSPORT_DATA = 0x4


class ChatClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, loop, signals, shutdown_event):
        self.loop = loop
        self.signals = signals
        self.shutdown_event = shutdown_event
        self.transport = None
        self.server_address = (SERVER_HOST, SERVER_PORT)
        self.chat_session_id = None
        self.chat_username = None
        self.chat_connected_event = asyncio.Event()
        self.wg_handshake_complete = False
        self.wg_C_initiator = None
        self.wg_H_initiator = None
        self.wg_E_priv_initiator = None
        self.wg_I_initiator = None
        self.wg_T_send = None
        self.wg_T_recv = None
        self.wg_N_send = 0
        self.wg_N_recv_latest = -1
        self.wg_I_responder = None
        # print("ChatClientProtocol Initialized (for Wireguard)") 

    def connection_made(self, transport):
        self.transport = transport
        self.signals.message_received.emit(f"Connecting to {self.server_address[0]}:{self.server_address[1]}...", "info")
        self.start_wireguard_handshake()

    def start_wireguard_handshake(self):
        # print("WG: Constructing Initiation Message...") 
        try:
            C_i = Hash(CONSTRUCTION)
            H_i = Hash(C_i + IDENTIFIER)
            H_i = Hash(H_i + SERVER_STATIC_PUBLIC_KEY_BYTES)
            self.wg_E_priv_initiator, E_pub_i = DH_Generate()
            C_i = Kdf1(C_i, E_pub_i)
            H_i = Hash(H_i + E_pub_i) 
            dh_eI_sR = DH(self.wg_E_priv_initiator, SERVER_STATIC_PUBLIC_KEY_BYTES)
            C_i, kappa1 = Kdf2(C_i, dh_eI_sR)
            msg_static = AEAD_encrypt(kappa1, 0, YOUR_STATIC_PUBLIC_KEY_BYTES, H_i)
            H_i = Hash(H_i + msg_static)
            dh_sI_sR = DH(YOUR_STATIC_PRIVATE_KEY_BYTES, SERVER_STATIC_PUBLIC_KEY_BYTES)
            C_i, kappa2 = Kdf2(C_i, dh_sI_sR)
            msg_timestamp = AEAD_encrypt(kappa2, 0, Timestamp(), H_i)
            H_i = Hash(H_i + msg_timestamp)
            self.wg_C_initiator = C_i
            self.wg_H_initiator = H_i
            self.wg_I_initiator = random.randint(0, 2**32 - 1)
            packed_sender_index = struct.pack("<I", self.wg_I_initiator)
            msg_a = (bytes([WG_MSG_TYPE_INITIATION]) + b'\x00\x00\x00' +
                     packed_sender_index + E_pub_i + msg_static + msg_timestamp)
            mac1_key = Hash(LABEL_MAC1 + SERVER_STATIC_PUBLIC_KEY_BYTES)
            mac1 = Mac(mac1_key, msg_a)
            initiation_packet = msg_a + mac1 + (b'\x00' * 16) 
            # print(f"WG: Sending Initiation Packet (len: {len(initiation_packet)} bytes)") 
            self.transport.sendto(initiation_packet)
        except Exception as e:
            self.signals.message_received.emit(f"WG Handshake Initiation Error: {e}", "error_critical")
            traceback.print_exc() 
            self.shutdown_event.set()

    def process_wireguard_handshake_response(self, response_packet: bytes):
        # print("WG: Processing Handshake Response...") 
        try:
            if len(response_packet) != 92:
                raise ValueError(f"WG Response: Incorrect packet length {len(response_packet)}, expected 92.")
            msg_type_resp = response_packet[0]
            server_sender_idx_bytes = response_packet[4:8]
            client_receiver_idx_bytes = response_packet[8:12]
            E_pub_r = response_packet[12:44]
            encrypted_empty_tag = response_packet[44:60]
            received_mac1 = response_packet[60:76]

            if msg_type_resp != WG_MSG_TYPE_RESPONSE:
                raise ValueError(f"WG Response: Incorrect message type {msg_type_resp}")
            client_receiver_idx = struct.unpack("<I", client_receiver_idx_bytes)[0]
            if client_receiver_idx != self.wg_I_initiator:
                raise ValueError(f"WG Response: Receiver index mismatch. Expected {self.wg_I_initiator}, got {client_receiver_idx}")
            self.wg_I_responder = struct.unpack("<I", server_sender_idx_bytes)[0]

            mac1_key = Hash(LABEL_MAC1 + YOUR_STATIC_PUBLIC_KEY_BYTES) 
            calculated_mac1_resp = Mac(mac1_key, response_packet[0:60])
            if calculated_mac1_resp != received_mac1:
                raise ValueError("WG Response: mac1 verification failed.")
            # print("WG: mac1 verified.") 

            C_r, H_r = self.wg_C_initiator, self.wg_H_initiator
            C_r = Kdf1(C_r, E_pub_r)
            H_r = Hash(H_r + E_pub_r)
            C_r = Kdf1(C_r, DH(self.wg_E_priv_initiator, E_pub_r))
            C_r = Kdf1(C_r, DH(YOUR_STATIC_PRIVATE_KEY_BYTES, E_pub_r))
            C_r, tau, kappa_for_empty = Kdf3(C_r, b'\x00'*32) 
            H_r = Hash(H_r + tau)
            decrypted_empty = AEAD_decrypt(kappa_for_empty, 0, encrypted_empty_tag, H_r)
            if decrypted_empty != b'':
                raise ValueError("WG Response: AEAD verification of 'empty' field failed.")
            # print("WG: 'empty' field AEAD verified.") 
            H_r = Hash(H_r + decrypted_empty)

            self.wg_T_send, self.wg_T_recv = Kdf2(C_r, b'')
            self.wg_handshake_complete = True
            self.signals.message_received.emit("Secure channel established.", "info_highlight") 
            # print(f"  WG Send Key: {self.wg_T_send.hex()}") 
            # print(f"  WG Recv Key: {self.wg_T_recv.hex()}") 
            self.wg_E_priv_initiator = self.wg_C_initiator = None 
            self.send_chat_connect()
        except ValueError as e: 
            self.signals.message_received.emit(f"WG Handshake Response Error: {e}", "error_critical")
            self.shutdown_event.set()
        except Exception as e: 
            self.signals.message_received.emit(f"WG: Unexpected error processing Response: {e}", "error_critical")
            traceback.print_exc() 
            self.shutdown_event.set()

    def send_chat_connect(self):
        if not self.wg_handshake_complete: return
        # print("WG: Sending initial CHAT CONNECT message.") 
        self.send_chat_message_via_wg({'request_type': CONNECT})

    def send_chat_message_via_wg(self, chat_data_dict: dict):
        if not self.wg_handshake_complete or not self.transport or self.transport.is_closing():
            self.signals.message_received.emit("Cannot send message: Connection not ready.", "error")
            return
        
        if self.chat_session_id is not None and chat_data_dict.get('request_type') != CONNECT:
            chat_data_dict['session'] = self.chat_session_id
        
        if 'request_handle' not in chat_data_dict:
             chat_data_dict['request_handle'] = random.randint(0, 2**32 - 1)
        
        print(f"WG OUT (CHAT): {chat_data_dict}") 
        try:
            chat_payload_msgpack = msgpack.packb(chat_data_dict)
            receiver_idx_bytes = struct.pack("<I", self.wg_I_responder)
            counter_bytes = struct.pack("<Q", self.wg_N_send)
            encrypted_chat_packet = AEAD_encrypt(self.wg_T_send, self.wg_N_send, chat_payload_msgpack, b'')
            wg_transport_message = (bytes([WG_MSG_TYPE_TRANSPORT_DATA]) + b'\x00\x00\x00' +
                                    receiver_idx_bytes + counter_bytes + encrypted_chat_packet)
            self.transport.sendto(wg_transport_message)
            self.wg_N_send += 1
        except Exception as e:
            self.signals.message_received.emit(f"Error sending WG Transport Data: {e}", "error")
            traceback.print_exc()

    def datagram_received(self, data: bytes, addr: tuple):
        msg_type = data[0]
        if not self.wg_handshake_complete:
            if msg_type == WG_MSG_TYPE_RESPONSE: self.process_wireguard_handshake_response(data)
            else: self.signals.message_received.emit(f"WG: Unexpected msg type {hex(msg_type)} during handshake.", "error_critical"); self.shutdown_event.set()
            return

        if msg_type == WG_MSG_TYPE_TRANSPORT_DATA:
            try:
                if len(data) < 16: raise ValueError("WG Transport: Packet too short for header.")
                counter = struct.unpack("<Q", data[8:16])[0]
                encrypted_payload_with_tag = data[16:]
                
                if counter <= self.wg_N_recv_latest and self.wg_N_recv_latest != -1:
                    self.signals.message_received.emit(f"WG: Replay/out-of-order packet (nonce {counter}). Ignored.", "info") 
                    return
                self.wg_N_recv_latest = counter
                
                decrypted_payload = AEAD_decrypt(self.wg_T_recv, counter, encrypted_payload_with_tag, b'')
                decoded_chat_msg = msgpack.unpackb(decrypted_payload, raw=False)
                print(f"CHAT IN: {decoded_chat_msg}") # UNCOMMENT THIS FOR DEBUGGING INCOMING MESSAGES
                self.handle_chat_protocol_message(decoded_chat_msg)
            except ValueError as e: self.signals.message_received.emit(f"WG Transport Decryption/Validation Error: {e}", "error")
            except msgpack.UnpackException as e: self.signals.message_received.emit(f"CHAT: Msgpack decode error: {e}", "error")
            except Exception as e: self.signals.message_received.emit(f"Error processing WG Transport Data: {e}", "error_critical"); traceback.print_exc()
        else: self.signals.message_received.emit(f"WG: Unexpected msg type {hex(msg_type)} after handshake.", "error")

    def handle_chat_protocol_message(self, msg: dict):
        res_type = msg.get('response_type')
        response_handle = msg.get('response_handle')
        
        if res_type == CONNECT_RESPONSE:
            self.chat_session_id = msg.get('session')
            self.chat_username = msg.get('username')
            self.signals.connection_state_changed.emit("connected", {
                "username": self.chat_username, "session": self.chat_session_id, "message": msg.get('message', '')
            })
            self.chat_connected_event.set()
            # Request user list after connecting
            self.send_chat_message_via_wg({'request_type': USER_LIST})
        elif res_type == OK: self.signals.message_received.emit(f"Server OK: {escape(msg.get('message', ''))}", "info_success")
        elif res_type == ERROR: self.signals.message_received.emit(f"SERVER ERROR: {escape(msg.get('error', 'Unknown error'))}", "error_critical")
        
        elif res_type == CHANNEL_CREATE_RESPONSE:
            channel_name = escape(msg.get('channel', 'N/A'))
            description = msg.get('description') 
            msg_text = f"Channel '{channel_name}' created."
            if description:
                msg_text += f" Description: '{escape(description)}'"
            self.signals.message_received.emit(msg_text, "info_highlight")

        elif res_type == CHANNEL_LIST_RESPONSE:
            channels = [escape(ch) for ch in msg.get('channels', [])]
            html = "<b>Available Channels:</b><br>" + ("<br>".join(f"- {ch}" for ch in channels) if channels else "(None found)")
            if msg.get('next_page'): html += "<br>(More channels available...)"
            self.signals.message_received.emit(html, "info_html")
            # Add this new line to emit signal for channel list update
            self.signals.channel_list_updated.emit(msg.get('channels', []))

        elif res_type == CHANNEL_INFO_RESPONSE:
            channel_name_from_msg = msg.get('channel')
            description_from_msg = msg.get('description')
            members_list_from_msg = msg.get('members')
            channel_name_display = escape(channel_name_from_msg if channel_name_from_msg is not None else 'N/A')
            description_display = escape(description_from_msg if description_from_msg is not None else '(No description)')
            if isinstance(members_list_from_msg, list):
                members_escaped = escape(', '.join(members_list_from_msg) if members_list_from_msg else 'N/A')
            else:
                members_escaped = 'N/A (invalid members format)'
            html = f"<b>Channel Info: {channel_name_display}</b><br>"
            html += f"Description: {description_display}<br>"
            html += f"Members: {members_escaped}"
            self.signals.message_received.emit(html, "info_html")
            
        elif res_type == CHANNEL_JOIN_RESPONSE:
            channel_name = escape(msg.get('channel', 'N/A'))
            user_who_joined = msg.get('username')
            topic_or_desc = msg.get('topic', msg.get('description')) 
            if not topic_or_desc and isinstance(msg.get('info'), dict): 
                topic_or_desc = msg.get('info').get('topic', msg.get('info').get('description'))
            
            if response_handle:
                join_msg_text = f"Joined channel '{channel_name}'."
                if topic_or_desc:
                    join_msg_text += f" Topic/Description: '{escape(topic_or_desc)}'"
                self.signals.message_received.emit(join_msg_text, "info_highlight")

                if msg.get('messages'):
                    recent_html = "<b>Recent messages in this channel:</b><br>"
                    for m_data in msg.get('messages', []):
                        recent_html += f"<{escape(m_data.get('from_user_in_channel', '?'))}> {escape(m_data.get('message', ''))}<br>"
                    self.signals.message_received.emit(recent_html, "info_html")
                # Update user list after joining a channel
                self.send_chat_message_via_wg({'request_type': USER_LIST})
            
            elif user_who_joined and user_who_joined != self.chat_username:
                join_notification_text = f"User '{escape(user_who_joined)}' joined channel '{channel_name}'."
                self.signals.message_received.emit(join_notification_text, "info")
                # Update user list after someone else joins
                self.send_chat_message_via_wg({'request_type': USER_LIST})
            elif user_who_joined and user_who_joined == self.chat_username and not response_handle:
                print(f"DEBUG: Own join notification (unsolicited) for channel '{channel_name}', likely already handled: {msg}")

        elif res_type == CHANNEL_LEFT_RESPONSE:
            channel_name = escape(msg.get('channel', 'N/A'))
            user_who_left = msg.get('username')

            if response_handle:
                self.signals.message_received.emit(f"Left channel '{escape(channel_name)}'.", "info")
                # Update user list after leaving a channel
                self.send_chat_message_via_wg({'request_type': USER_LIST})
            elif user_who_left:
                if user_who_left == self.chat_username:
                    self.signals.message_received.emit(f"You were removed from channel '{channel_name}'.", "info_highlight")
                else:
                    leave_notification_text = f"User '{escape(user_who_left)}' left channel '{channel_name}'."
                    self.signals.message_received.emit(leave_notification_text, "info")
                # Update user list after someone leaves
                self.send_chat_message_via_wg({'request_type': USER_LIST})

        elif res_type == WHOIS_RESPONSE:
            session_id_val = 'N/A' # Initialize session_id_val

            if 'info' in msg:
                info = msg.get('info', {})
                username_val = info.get('username', 'N/A')
                status_val = 'Online' if info.get('online') else 'Offline'
                channels_val = info.get('channels', [])
                transport_val = 'Unknown'
                wg_public_key_str = 'N/A'
                session_id_val = str(info.get('session_id', info.get('session', 'N/A'))) # Get session from info
            else:
                #direct fields
                username_val = msg.get('username', 'N/A')
                status_val = msg.get('status', 'N/A')
                channels_val = msg.get('channels', []) 
                transport_val = msg.get('transport', 'N/A')
                session_id_val = str(msg.get('session_id', msg.get('session', 'N/A'))) # Get session from msg root
                
                #Handle WireGuard public key properly
                wg_public_key_data = msg.get('wireguard_public_key', b'')
                wg_public_key_str = 'N/A'
                
                if isinstance(wg_public_key_data, bytes):
                    if len(wg_public_key_data) > 0:
                        try:
                            wg_public_key_str = wg_public_key_data.decode('utf-8')
                        except UnicodeDecodeError:
                            wg_public_key_str = wg_public_key_data.hex()
                elif isinstance(wg_public_key_data, str): 
                    wg_public_key_str = wg_public_key_data

            #Build comprehensive HTML response
            
            html = f"<b>Whois: {escape(username_val)}</b><br>"
            html += f"Status: {escape(status_val.capitalize())}<br>"
            html += f"Session ID: {escape(session_id_val)}<br>"
            html += f"Channels: {escape(', '.join(channels_val)) if channels_val else 'N/A'}<br>"
            html += f"Transport: {escape(transport_val.capitalize())}<br>"
            
            # Only show WireGuard key if transport is WireGuard and we have a key
            if transport_val.lower() == 'wireguard' and wg_public_key_str != 'N/A':
                html += f"WireGuard Public Key: {escape(wg_public_key_str)}"
            
            self.signals.message_received.emit(html, "info_html")
            print(f"WHOIS_DEBUG: Processed WHOIS for {username_val}, transport: {transport_val}")

        elif res_type == WHOAMI_RESPONSE:
            #Get username - try multiple possible field names for compatibility
            username = msg.get('username') or msg.get('user') or self.chat_username or 'N/A'
            session = msg.get('session') or msg.get('session_id') or self.chat_session_id or 'N/A'
            
            html = f"<b>You are: {escape(str(username))}</b><br>"
            html += f"Session ID: {escape(str(session))}<br>"
            
            #Add additional info if available
            if msg.get('status'):
                html += f"Status: {escape(str(msg.get('status')))}<br>"
            if msg.get('channels'):
                channels = msg.get('channels')
                if isinstance(channels, list):
                    html += f"Joined Channels: {escape(', '.join(channels)) if channels else 'None'}<br>"
            if msg.get('transport'):
                html += f"Transport: {escape(str(msg.get('transport')))}<br>"
                
            self.signals.message_received.emit(html, "info_html")
        
        elif res_type == SET_USERNAME_RESPONSE: # Type 34
            new_name = msg.get('new_username', msg.get('username')) 
            old_name = msg.get('old_username') 
            response_handle = msg.get('response_handle')
            
            if response_handle and new_name: # Our request succeeded
                self.chat_username = new_name # Update local state for OUR username
                self.signals.message_received.emit(f"Username changed to '{escape(new_name)}'.", "info_highlight")
                self.signals.title_updated.emit(f"WG Chat - {escape(new_name)}@{SERVER_HOST}")
                self.signals.status_updated.emit(f"Connected as {escape(new_name)} (Session: {self.chat_session_id})")
                
                # Force immediate user list update - explicitly request from server
                self.send_chat_message_via_wg({'request_type': USER_LIST})
                
                # Also update the local user list directly if available
                if hasattr(self, '_last_user_list') and isinstance(self._last_user_list, list):
                    # Find and replace our username in the local list
                    if old_name and old_name in self._last_user_list:
                        self._last_user_list.remove(old_name)
                    if new_name not in self._last_user_list:
                        self._last_user_list.append(new_name)
                    # Emit the updated list
                    self.signals.user_list_updated.emit(self._last_user_list)
            
            elif old_name and new_name and old_name != self.chat_username: # Notification about someone else
                self.signals.message_received.emit(f"User '{escape(old_name)}' is now known as '{escape(new_name)}'.", "info")
                
                # Update local username list if we have it cached
                if hasattr(self, '_last_user_list') and isinstance(self._last_user_list, list):
                    if old_name in self._last_user_list:
                        self._last_user_list.remove(old_name)
                    if new_name not in self._last_user_list:
                        self._last_user_list.append(new_name)
                    self.signals.user_list_updated.emit(self._last_user_list)
                    
                # Also request a fresh list from server
                self.send_chat_message_via_wg({'request_type': USER_LIST})
            
            elif new_name and not old_name and not response_handle: 
                if self.chat_username != new_name: 
                    self.chat_username = new_name
                    self.signals.message_received.emit(f"Username is now '{escape(new_name)}'.", "info_highlight")
                    self.signals.title_updated.emit(f"WG Chat - {escape(new_name)}@{SERVER_HOST}")
                    self.signals.status_updated.emit(f"Connected as {escape(new_name)} (Session: {self.chat_session_id})")
                    self.send_chat_message_via_wg({'request_type': USER_LIST})
            else: 
                self.signals.message_received.emit(f"Set username acknowledged: {escape(msg.get('message', ''))}", "info")

        # Now also enhance the USER_LIST_RESPONSE handler to cache the user list
        elif res_type == USER_LIST_RESPONSE:
            user_list = msg.get('users', [])
            next_page = msg.get('next_page', False)
            # Cache the user list for use in other handlers
            self._last_user_list = user_list
            # Make sure our own username is in the list
            if self.chat_username and self.chat_username not in user_list:
                user_list.append(self.chat_username)
                self._last_user_list = user_list
            self.signals.user_list_updated.emit(user_list)

            # Show pagination information if there are more users
            if next_page:
                current_count = len(user_list)
                self.signals.message_received.emit(
                    f"Showing {current_count} users. More users available - use '/users {current_count}' to see the next page.", 
                    "info_highlight"
                )
        
        elif res_type == SERVER_MESSAGE:
            tag, display_message = "server", ""
            text = msg.get('message')
            from_ch = msg.get('from_channel')
            from_user_ch = msg.get('from_user_in_channel')
            from_user_dm = msg.get('from_user')
            to_user_dm = msg.get('to_user')

            if from_ch and from_user_ch and text:
                # ALWAYS tag channel messages as "channel" - let GUI handle display logic
                tag = "channel"
                display_message = f"[{escape(from_ch)}] <{escape(from_user_ch)}>: {escape(text)}"
            elif from_user_dm and to_user_dm == self.chat_username and text:
                tag = "dm"
                display_message = f"[PM from {escape(from_user_dm)}]: {escape(text)}"
            elif text: 
                display_message = f"[SERVER]: {escape(text)}"
            else: 
                display_message = f"Received unhandled SERVER_MESSAGE format: {escape(str(msg))}"; tag="error"
            self.signals.message_received.emit(display_message, tag)

        elif res_type == SERVER_SHUTDOWN:
            reason = escape(msg.get('message', 'No reason given.'))
            self.signals.message_received.emit(f"!!! SERVER SHUTDOWN: {reason} !!!", "error_critical")
            self.signals.connection_state_changed.emit("shutdown", {"message": reason})
        
        elif res_type == CHANNEL_MESSAGE_RESPONSE:
            from_ch = msg.get('channel', msg.get('from_channel'))
            from_user = msg.get('username', msg.get('from_user_in_channel')) 
            text = msg.get('message')

            if from_ch and from_user and text:
                if from_user == self.chat_username: 
                    tag = "own_message"
                else:
                    tag = "channel"
                display_message = f"[{escape(from_ch)}] <{escape(from_user)}>: {escape(text)}"
                self.signals.message_received.emit(display_message, tag)

        elif res_type == USER_MESSAGE_RESPONSE:
            from_user_dm = msg.get('from_username')
            to_user_dm = msg.get('to_username')
            text = msg.get('message')
            
            # Special case for messaging yourself
            if from_user_dm == self.chat_username and (to_user_dm == self.chat_username or not to_user_dm):
                # Only show this for messages from the server (not our own sent confirmations)
                if not response_handle:
                    tag = "dm"
                    display_message = f"[From You]: {escape(text)}"
                    self.signals.message_received.emit(display_message, tag)
            
            # Normal case where you're the sender (confirmation of outgoing message)
            elif response_handle and from_user_dm == self.chat_username and to_user_dm:
                # We already displayed this when sent, so don't show it again
                pass
            
            # Normal case where you're the recipient
            elif from_user_dm and from_user_dm != self.chat_username and text:
                tag = "dm"
                display_message = f"[From {escape(from_user_dm)}]: {escape(text)}"
                self.signals.message_received.emit(display_message, tag)

        elif res_type == PING_RESPONSE: 
            pass 
        else: 
            self.signals.message_received.emit(f"Received unhandled response type: {res_type} | Data: {escape(str(msg))}", "error")
    
    def error_received(self, exc):
        self.signals.message_received.emit(f"Socket error: {exc}", "error_critical")
        if not self.wg_handshake_complete: self.shutdown_event.set()

    def connection_lost(self, exc):
        self.signals.message_received.emit(f"Connection lost. {('Exception: ' + str(exc)) if exc else ''}", "error_critical")
        self.signals.connection_state_changed.emit("disconnected", {})
        if not self.shutdown_event.is_set(): self.shutdown_event.set()

async def send_pings(protocol: ChatClientProtocol):
    while not protocol.shutdown_event.is_set():
        try:
            await asyncio.wait_for(protocol.chat_connected_event.wait(), timeout=None)
            if protocol.chat_session_id and protocol.transport and not protocol.transport.is_closing():
                protocol.send_chat_message_via_wg({'request_type': PING})
            await asyncio.sleep(PING_INTERVAL)
        except asyncio.CancelledError: break
        except Exception as e: 
            print(f"Error in PING task: {e}") 
            await asyncio.sleep(PING_INTERVAL)

def parse_and_send_command(message_input: str, protocol: ChatClientProtocol):
    if not message_input: return
    message_input = message_input.strip()
    
    if not protocol.wg_handshake_complete or \
       (not protocol.chat_connected_event.is_set() and not message_input.lower() == "/quit"):
        protocol.signals.message_received.emit("Cannot send command: Not fully connected.", "error")
        return
    if not protocol.transport or protocol.transport.is_closing():
        protocol.signals.message_received.emit("Cannot send command: Transport unavailable.", "error")
        return

    request = None
    cmd_parts = message_input.split(" ", 2) 
    cmd = cmd_parts[0].lower()
    arg1 = cmd_parts[1] if len(cmd_parts) > 1 else None
    arg2 = cmd_parts[2] if len(cmd_parts) > 2 else None 

    # print(f"Command Parse: cmd='{cmd}', arg1='{arg1}', arg2='{arg2}'") 

    if cmd == "/quit": 
        protocol.shutdown_event.set()
        protocol.signals.message_received.emit("Disconnecting...", "info")
        return # Exit after handling /quit
    elif cmd == "/help":
        help_text = """<b>Available Commands:</b><br>
        /users [offset] - List online users (offset optional for pagination)<br>
        /channels [offset] - List channels<br>
        /create <channel_name> [description] - Create a channel (description is optional)<br>
        /info <channel_name> - Get details about a channel<br>
        /join <channel_name> - Join a channel<br>
        /leave <channel_name> - Leave a channel<br>
        /say <channel_name> <message> - Send message to channel<br>
        /msg <username> <message> - Send direct message<br>
        /whois <username> - Get info about a user<br>
        /whoami - Get info about yourself<br>
        /setuser <new_username> - Change your username<br>
        /quit - Disconnect and exit"""
        protocol.signals.message_received.emit(help_text, "info_html")
        return # Exit after handling /help
    elif cmd == "/channels": 
        request = {'request_type': CHANNEL_LIST}
        if arg1: 
            try: 
                request['offset'] = int(arg1)
            except ValueError: 
                protocol.signals.message_received.emit("Usage: /channels [offset]", "error")
                return 
    elif cmd == "/users": 
        request = {'request_type': USER_LIST}
        if arg1: 
            try: 
                request['offset'] = int(arg1)
            except ValueError: 
                protocol.signals.message_received.emit("Usage: /users [offset]", "error")
                return 
    elif cmd == "/whois":
        if arg1: 
            request = {'request_type': WHOIS, 'username': arg1}
        else:
            protocol.signals.message_received.emit("Usage: /whois <username>", "error")
            return 
    elif cmd == "/whoami": 
        request = {'request_type': WHOAMI}
    elif cmd == "/setuser":
        if arg1: 
            request = {'request_type': SET_USERNAME, 'username': arg1}
        else:
            protocol.signals.message_received.emit("Usage: /setuser <new_username>", "error")
            return 
    elif cmd == "/create": 
        if arg1: 
            request = {'request_type': CHANNEL_CREATE, 'channel': arg1}
            if arg2: 
                request['description'] = arg2
        else:
            protocol.signals.message_received.emit("Usage: /create <channel_name> [description]", "error")
            return 
    elif cmd == "/join":
        if arg1: 
            request = {'request_type': CHANNEL_JOIN, 'channel': arg1}
        else:
            protocol.signals.message_received.emit("Usage: /join <channel_name>", "error")
            return 
    elif cmd == "/leave":
        if arg1: 
            request = {'request_type': CHANNEL_LEAVE, 'channel': arg1}
        else:
            protocol.signals.message_received.emit("Usage: /leave <channel_name>", "error")
            return 
    elif cmd == "/say":
        if arg1 and arg2: 
            request = {'request_type': CHANNEL_MESSAGE, 'channel': arg1, 'message': arg2}
        else:
            protocol.signals.message_received.emit("Usage: /say <channel_name> <message>", "error")
            return 
    elif cmd == "/msg":
        if arg1 and arg2: 
            request = {'request_type': USER_MESSAGE, 'to_username': arg1, 'message': arg2}
        else:
            protocol.signals.message_received.emit("Usage: /msg <username> <message>", "error")
            return
    
    elif cmd == "/info": 
        if arg1:
            request = {'request_type': CHANNEL_INFO, 'channel': arg1}
        else:
            protocol.signals.message_received.emit("Usage: /info <channel_name>", "error")
            return
    
    else: 
        # This 'else' is only reached if 'cmd' didn't match any known command pattern above.
        # If 'request' is still None here (which it will be if no prior elif matched),
        # it means it was an unknown command or non-command text.
        if cmd.startswith("/"): # Check if it looked like a command
            protocol.signals.message_received.emit(f"Unknown command: {cmd}. Type /help for commands.", "error")
        else: # Not a command
            protocol.signals.message_received.emit("Type a command (e.g., /users or /help) or use /say or /msg.", "error")
        return # Important to return here if no valid request was formed

    # This line is only reached if 'request' was successfully built by one of the elif blocks
    if request: 
        protocol.send_chat_message_via_wg(request)
    # else:
    # This case should ideally not be reached if all valid commands build a 'request'
    # and all invalid commands/text lead to a 'return' in the 'else' block above.
    # However, as a safeguard, you could add:
    # protocol.signals.message_received.emit(f"Internal error parsing command: {message_input}", "error")

async def main_cli(): 
    loop = asyncio.get_running_loop()
    print("Running client_logic.py in CLI mode - GUI signals are not available.")
    class DummySignals:
        def emit(self, *args): print(f"CLI_MODE_SIGNAL: {args}")
    class DummyWindowSignals:
        message_received = status_updated = connection_state_changed = title_updated = user_list_updated = DummySignals()
    cli_signals = DummyWindowSignals()
    shutdown_event = asyncio.Event()
    
    print(f"CLI: Attempting to connect to UDP {SERVER_HOST}:{SERVER_PORT} (Wireguard)")
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: ChatClientProtocol(loop, cli_signals, shutdown_event),
            remote_addr=(SERVER_HOST, SERVER_PORT))
    except OSError as e: print(f"CLI: Error creating socket: {e}"); return
    except Exception as e: print(f"CLI: Unexpected error creating endpoint: {e}"); traceback.print_exc(); return

    ping_task = asyncio.create_task(send_pings(protocol))
    input_task = asyncio.create_task(handle_user_input_cli(protocol))
    try:
        await shutdown_event.wait()
        print("CLI: Shutdown signal received, cleaning up...")
    finally:
        print("CLI: Cancelling tasks...");
        for task in [ping_task, input_task]:
            if task and not task.done(): task.cancel()
        await asyncio.gather(ping_task, input_task, return_exceptions=True)

        if protocol.wg_handshake_complete and protocol.chat_session_id and \
           protocol.transport and not protocol.transport.is_closing():
            print("CLI: Sending CHAT DISCONNECT via Wireguard...")
            protocol.send_chat_message_via_wg({'request_type': DISCONNECT})
            await asyncio.sleep(0.2)

        print("CLI: Closing transport...");
        if transport and not transport.is_closing(): transport.close()
        print("CLI Client finished.")

async def handle_user_input_cli(protocol: ChatClientProtocol):
    loop = asyncio.get_running_loop()
    while not protocol.shutdown_event.is_set():
        try:
            if not protocol.chat_connected_event.is_set() and not protocol.wg_handshake_complete:
                await asyncio.sleep(0.1); continue
            elif not protocol.chat_connected_event.is_set() and protocol.wg_handshake_complete:
                sys.stdout.write("(CLI: Waiting for CHAT CONNECT_RESPONSE... Type /quit to exit) > "); sys.stdout.flush()
            else: 
                sys.stdout.write("(CLI) > "); sys.stdout.flush()
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if protocol.shutdown_event.is_set(): break
            parse_and_send_command(line, protocol)
        except asyncio.CancelledError: break
        except EOFError: print("\nCLI: EOF received, shutting down."); protocol.shutdown_event.set(); break
        except Exception as e: print(f"CLI: Error reading user input: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main_cli())
    except KeyboardInterrupt: print("\nCaught KeyboardInterrupt, shutting down CLI...")
    except Exception as e:
        print(f"\nUnhandled error in CLI main: {e}"); traceback.print_exc()
    finally:
        print("Exiting CLI program.")