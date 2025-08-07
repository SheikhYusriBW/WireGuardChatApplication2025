# Enhanced gui_chat.py with Main/Chat screen switcher and history export - COMPLETE VERSION
import asyncio
import sys
import re
from html import escape as html_escape
from datetime import datetime
import asyncqt
import traceback
from collections import defaultdict, OrderedDict
import os

from PyQt5.QtWidgets import (QApplication, QMainWindow, QTextBrowser, QLineEdit, 
                             QPushButton, QVBoxLayout, QHBoxLayout, QWidget, 
                             QStatusBar, QMessageBox, QSplitter, QListWidget, 
                             QListWidgetItem, QCheckBox, QTabWidget, QMenu,
                             QLabel, QFrame, QScrollArea, QSizePolicy, QButtonGroup)
from PyQt5.QtCore import pyqtSignal, pyqtSlot, Qt, QObject, QSize
from PyQt5.QtGui import QFont, QIcon, QColor, QBrush, QPalette

try:
    from client_logic import (ChatClientProtocol, send_pings, parse_and_send_command, 
                              SERVER_HOST, SERVER_PORT, DISCONNECT)
    print("Successfully imported from client_logic.py")
except ImportError as e:
    print(f"Error importing from client_logic.py: {e}")
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred during import from client_logic.py: {e}")
    traceback.print_exc()
    sys.exit(1)

class WindowSignals(QObject):
    message_received = pyqtSignal(str, str)
    status_updated = pyqtSignal(str)
    connection_state_changed = pyqtSignal(str, dict)
    title_updated = pyqtSignal(str)
    user_list_updated = pyqtSignal(list)
    channel_list_updated = pyqtSignal(list)
    
    # New signals for conversation management
    new_conversation_message = pyqtSignal(str, str, str)  # conversation_id, message, tag
    conversation_joined = pyqtSignal(str, str)  # conversation_id, name
    conversation_left = pyqtSignal(str)  # conversation_id

    channel_members_updated = pyqtSignal(str, list)  # channel_name, members_list

class ConversationItem(QFrame):
    clicked = pyqtSignal(str)  # conversation_id
    
    def __init__(self, conv_id, name, is_channel=False, parent=None):
        super().__init__(parent)
        self.conv_id = conv_id
        self.is_channel = is_channel
        self.unread_count = 0
        # self._suppress_sidebar_tab_action = False # Add this line
        
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            ConversationItem {
                background-color: #2A2A2A;
                border-radius: 4px;
                margin: 2px;
                padding: 8px;
            }
            ConversationItem:hover {
                background-color: #3A3A3A;
            }
            ConversationItem[selected="true"] {
                background-color: #404040;
                border-left: 3px solid #007ACC;
            }
        """)
        self.setProperty("selected", "false")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Conversation icon/prefix
        self.icon_label = QLabel("💬" if not is_channel else "👥")
        self.icon_label.setStyleSheet("color: #8C8C8C; font-size: 16px;")
        layout.addWidget(self.icon_label)
        
        # Conversation name and preview
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet("color: #FFFFFF; font-weight: bold;")
        text_layout.addWidget(self.name_label)
        
        self.preview_label = QLabel("No messages yet")
        self.preview_label.setStyleSheet("color: #A0A0A0; font-size: 9pt;")
        self.preview_label.setWordWrap(True)
        text_layout.addWidget(self.preview_label)
        
        layout.addLayout(text_layout, 1)
        
        # Unread indicator
        self.unread_label = QLabel("")
        self.unread_label.setStyleSheet("""
            background-color: #007ACC; 
            color: white; 
            border-radius: 10px; 
            padding: 2px 6px;
            min-width: 20px; 
            max-width: 20px; 
            min-height: 20px; 
            max-height: 20px; 
            text-align: center;
        """)
        self.unread_label.setAlignment(Qt.AlignCenter)
        self.unread_label.hide()
        layout.addWidget(self.unread_label)
        
    def mousePressEvent(self, event):
        self.clicked.emit(self.conv_id)
        super().mousePressEvent(event)
        
    def set_selected(self, selected):
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        
    def update_preview(self, text):
        try:
            in_tag = False
            plain_text = ""
            for char in text[:1000]:  # Limit processing to first 1000 chars for safety
                if char == '<':
                    in_tag = True
                elif char == '>':
                    in_tag = False
                elif not in_tag:
                    plain_text += char
            
            # Limit preview length
            preview = plain_text.strip()
            if len(preview) > 30:
                preview = preview[:27] + "..."
            
            self.preview_label.setText(preview)
        except Exception as e:
            # Fallback in case of any error
            print(f"Error generating preview: {e}")
            self.preview_label.setText("New message")
        
    def increment_unread(self):
        self.unread_count += 1
        self.unread_label.setText(str(self.unread_count))
        self.unread_label.show()
        
    def clear_unread(self):
        self.unread_count = 0
        self.unread_label.hide()

class ChatWindow(QMainWindow):
    def __init__(self, loop, shutdown_event):
        super().__init__()
        self.loop = loop
        self.shutdown_event = shutdown_event
        self.signals = WindowSignals()
        self.send_command_func = None
        
        # Track conversations (channels and direct messages)
        self.conversations = {}  # conv_id -> ConversationItem
        self.conversation_history = {}  # conv_id -> list of (timestamp, message, tag)
        self.main_history = []  # Main screen history
        self.active_conversation = None
        
        # Track current screen mode
        self.current_screen = "main"  # "main" or "chat"
        
        # Track channel members
        self.channel_members = {}  # channel_name -> list of members
        
        # Track if we're showing conversation view or main chat
        self.conversation_mode = False

        self._suppress_sidebar_tab_action = False
        
        self.init_ui()
        self.connect_signals()
        self.signals.message_received.emit("GUI Initialized. Welcome to WireGuard Chat!", "info_highlight")
        self.signals.message_received.emit("Main Screen - Use commands like /users, /channels, /join, etc.", "info")
        self.enhance_user_list_right_click()

    def init_ui(self):
        self.setWindowTitle(f"WG Chat - Connecting...")
        self.setGeometry(100, 100, 1200, 700)
        self.setStyleSheet("""
            QMainWindow { background-color: #1E1E1E; }
            QTextBrowser { 
                background-color: #1E1E1E; 
                color: #D4D4D4; 
                border: none;
                font-size: 10pt;
            }
            QListWidget { 
                background-color: #252526; 
                color: #CCCCCC; 
                border: none;
                font-size: 9pt;
            }
            QLineEdit { 
                background-color: #3C3C3C; 
                color: #F0F0F0; 
                border: 1px solid #5A5A5A; 
                border-radius: 4px;
                padding: 8px;
                font-size: 10pt;
            }
            QPushButton { 
                background-color: #007ACC; 
                color: white; 
                border: none; 
                border-radius: 4px;
                padding: 8px 12px; 
                font-size: 10pt;
                min-width: 60px;
            }
            QPushButton:hover { background-color: #005C99; }
            QPushButton:pressed { background-color: #004C80; }
            QPushButton:checked { 
                background-color: #004C80; 
                font-weight: bold;
            }
            QStatusBar { color: #A0A0A0; font-size: 9pt;}
            QCheckBox { color: #B0B0B0; font-size: 9pt; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { 
                background-color: #2D2D2D; 
                color: #A0A0A0; 
                border: none;
                padding: 8px 12px;
            }
            QTabBar::tab:selected { 
                background-color: #1E1E1E; 
                color: #FFFFFF; 
                border-bottom: 2px solid #007ACC;
            }
            QScrollArea { border: none; background-color: #252526; }
            QScrollBar:vertical {
                background-color: #2A2A2A;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #5A5A5A;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Left panel - Screen switcher and content
        left_panel = QWidget()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background-color: #252526;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        # Header with screen switcher buttons
        header = QWidget()
        header.setStyleSheet("background-color: #323233;")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 10, 10, 10)
        
        # Screen switcher buttons
        button_layout = QHBoxLayout()
        self.button_group = QButtonGroup()
        
        self.main_screen_button = QPushButton("📋 Main")
        self.main_screen_button.setCheckable(True)
        self.main_screen_button.setChecked(True)
        self.main_screen_button.setStyleSheet("""
            QPushButton {
                background-color: #404040;
                color: white;
                font-size: 12px;
                padding: 8px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:checked {
                background-color: #007ACC;
                border: 2px solid #00A0FF;
                font-weight: bold;                                                        
            }
        """)
        self.button_group.addButton(self.main_screen_button)
        button_layout.addWidget(self.main_screen_button)
        
        self.chat_screen_button = QPushButton("💬 Chats")
        self.chat_screen_button.setCheckable(True)
        self.chat_screen_button.setStyleSheet("""
            QPushButton {
                background-color: #404040;
                color: white;
                font-size: 12px;
                padding: 8px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
            QPushButton:checked {
                background-color: #007ACC;
                border: 2px solid #00A0FF;
                font-weight: bold;
            }
        """)
        self.button_group.addButton(self.chat_screen_button)
        button_layout.addWidget(self.chat_screen_button)
        
        header_layout.addLayout(button_layout)
        left_layout.addWidget(header)
        
        # Content area that switches between screens
        self.content_stack = QWidget()
        content_stack_layout = QVBoxLayout(self.content_stack)
        content_stack_layout.setContentsMargins(0, 0, 0, 0)
        
        # Main screen content
        self.main_screen_content = self.create_main_screen()
        content_stack_layout.addWidget(self.main_screen_content)
        
        # Chat screen content
        self.chat_screen_content = self.create_chat_screen()
        self.chat_screen_content.hide()
        content_stack_layout.addWidget(self.chat_screen_content)
        
        left_layout.addWidget(self.content_stack)
        
        # Right panel - Chat area (with members panel)
        right_panel = QWidget()
        self.right_panel_layout = QHBoxLayout(right_panel)
        self.right_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.right_panel_layout.setSpacing(0)
        
        # Chat content (message area)
        chat_content = QWidget()
        chat_layout = QVBoxLayout(chat_content)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        
        # Chat header
        self.chat_header = QWidget()
        self.chat_header.setFixedHeight(50)
        self.chat_header.setStyleSheet("background-color: #323233;")
        chat_header_layout = QHBoxLayout(self.chat_header)
        chat_header_layout.setContentsMargins(15, 0, 15, 0)
        
        self.chat_header_label = QLabel("Main Command Center")
        self.chat_header_label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        chat_header_layout.addWidget(self.chat_header_label)
        
        # Add members button for channels
        self.members_button = QPushButton("👥 Members")
        self.members_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0A0A0;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #404040;
                color: white;
            }
        """)
        self.members_button.clicked.connect(lambda: self.show_channel_members())
        self.members_button.hide()  # Hidden initially
        chat_header_layout.addWidget(self.members_button)
        
        # Add history export button
        self.history_button = QPushButton("📄 Export History")
        self.history_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A0A0A0;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #404040;
                color: white;
            }
        """)
        self.history_button.clicked.connect(self.export_current_history)
        chat_header_layout.addWidget(self.history_button)
        
        chat_layout.addWidget(self.chat_header)
        
        # Chat area
        self.text_area = QTextBrowser()
        self.text_area.setReadOnly(True)
        self.text_area.setOpenExternalLinks(True)
        self.text_area.document().setDefaultStyleSheet("""
            p { margin-bottom: 8px; line-height: 140%; }
            a { color: #60AFFF; text-decoration: none; }
            .timestamp { color: #777; font-size: smaller; }
            .info { color: #87CEEB; } 
            .info_highlight { color: #90EE90; font-weight: bold; } 
            .info_success { color: #98FB98; } 
            .info_html { color: #ADD8E6; } 
            .error { color: #FF7F7F; font-style: italic;} 
            .error_critical { color: #FF6347; font-weight: bold; } 
            .server { color: #DA70D6; font-weight: bold; } 
            .channel { color: #46C7C7; } 
            .dm { color: #FFB347; } 
            .own_message { color: #B0B0B0; font-style: italic; } 
        """)
        chat_layout.addWidget(self.text_area)
        
        # Input area
        input_widget = QWidget()
        input_widget.setFixedHeight(70)
        input_widget.setStyleSheet("background-color: #2A2A2A;")
        input_layout = QHBoxLayout(input_widget)
        input_layout.setContentsMargins(15, 10, 15, 10)
        
        self.input_entry = QLineEdit()
        self.input_entry.setPlaceholderText("Type a message or command... (type /help for commands)")
        input_layout.addWidget(self.input_entry)
        
        self.send_button = QPushButton("Send")
        input_layout.addWidget(self.send_button)
        
        chat_layout.addWidget(input_widget)
        
        # Add chat content to right panel
        self.right_panel_layout.addWidget(chat_content, 1)
        
        # Add panels to main layout
        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel, 1)
        
        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.signals.status_updated.emit("Initializing...")
        
        # Side panels (preserved from original)
        self.sidebar = QTabWidget()
        self.sidebar.setTabPosition(QTabWidget.East)
        self.sidebar.setFixedWidth(0)  # Start hidden
        self.sidebar.setStyleSheet("""
            QTabWidget::tab-bar { alignment: center; }
            QTabWidget::pane { border: none; }
            QTabBar::tab { 
                background-color: #1A1A1A; 
                color: #A0A0A0; 
                padding: 10px;
                border: none; 
                min-width: 30px;
                max-width: 30px;
                min-height: 100px;
                margin: 5px 0px;
            }
            QTabBar::tab:selected { 
                background-color: #252526; 
                color: #FFFFFF;
                border-right: 2px solid #007ACC;
            }
        """)
        
        # User List (preserved from original)
        self.user_list_widget = QListWidget()
        self.sidebar.addTab(self.user_list_widget, "Users")
        
        # Channel List (preserved from original)
        self.channel_list_widget = QListWidget()
        self.sidebar.addTab(self.channel_list_widget, "Channels")

        # Add this line to connect the signal
        self.sidebar.currentChanged.connect(self.on_sidebar_tab_changed) # gui_chat.py
        
        main_layout.addWidget(self.sidebar)
        
        # Sidebar toggle button (preserved from original)
        self.sidebar_toggle = QPushButton("≡")
        self.sidebar_toggle.setStyleSheet("""
            QPushButton {
                background-color: #323233;
                color: white;
                border: none;
                font-size: 16px;
                font-weight: bold;
                min-width: 30px;
                max-width: 30px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #404040;
            }
        """)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        
        # Add sidebar toggle to main layout
        toggle_container = QWidget()
        toggle_container.setFixedWidth(30)
        toggle_container.setStyleSheet("background-color: #323233;")
        toggle_layout = QVBoxLayout(toggle_container)
        toggle_layout.setContentsMargins(0, 10, 0, 0)
        toggle_layout.setAlignment(Qt.AlignTop)
        toggle_layout.addWidget(self.sidebar_toggle)
        main_layout.addWidget(toggle_container)
        
        # Initialize the channel members panel
        self.init_channel_members_panel()
        
        # Connect screen switcher buttons
        self.main_screen_button.clicked.connect(lambda: self.switch_screen("main"))
        self.chat_screen_button.clicked.connect(lambda: self.switch_screen("chat"))
        
        # Initially disable input
        self.input_entry.setEnabled(False)
        self.send_button.setEnabled(False)

    def create_main_screen(self):
        """Create the main command screen content"""
        main_content = QWidget()
        main_layout = QVBoxLayout(main_content)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title_label = QLabel("🎛️ Command Center")
        title_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        main_layout.addWidget(title_label)
        
        # Quick actions section
        quick_actions_label = QLabel("Quick Actions:")
        quick_actions_label.setStyleSheet("color: #A0A0A0; font-weight: bold; margin-top: 10px;")
        main_layout.addWidget(quick_actions_label)
        
        # Quick action buttons
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(5)
        
        quick_buttons = [
            ("👥 List Users", "/users"),
            ("📋 List Channels", "/channels"), 
            ("🆔 Who Am I", "/whoami"),
            ("❓ Help", "/help")
        ]
        
        for text, command in quick_buttons:
            btn = QPushButton(text)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #404040;
                    color: white;
                    text-align: left;
                    padding: 8px 12px;
                    border-radius: 4px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #505050;
                }
            """)
            btn.clicked.connect(lambda checked, cmd=command: self.execute_quick_command(cmd))
            actions_layout.addWidget(btn)
        
        main_layout.addLayout(actions_layout)
        
        # Recent activity section
        recent_label = QLabel("Recent Commands:")
        recent_label.setStyleSheet("color: #A0A0A0; font-weight: bold; margin-top: 20px;")
        main_layout.addWidget(recent_label)
        
        self.recent_commands_list = QListWidget()
        self.recent_commands_list.setMaximumHeight(150)
        self.recent_commands_list.setStyleSheet("""
            QListWidget {
                background-color: #2A2A2A;
                border: 1px solid #404040;
                border-radius: 4px;
                color: #D4D4D4;
            }
            QListWidget::item {
                padding: 4px;
                border-bottom: 1px solid #3A3A3A;
            }
            QListWidget::item:selected {
                background-color: #007ACC;
            }
        """)
        main_layout.addWidget(self.recent_commands_list)
        
        # Add spacer to push content to top
        main_layout.addStretch()
        
        return main_content

    def create_chat_screen(self):
        """Create the chat conversations screen content"""
        chat_content = QWidget()
        chat_layout = QVBoxLayout(chat_content)
        chat_layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title_label = QLabel("💬 Conversations")
        title_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        chat_layout.addWidget(title_label)
        
        # Search box
        search_box = QLineEdit()
        search_box.setPlaceholderText("🔍 Search conversations...")
        search_box.setStyleSheet("""
            QLineEdit { 
                background-color: #3C3C3C; 
                border-radius: 15px;
                padding: 8px 12px;
                margin-bottom: 10px;
            }
        """)
        chat_layout.addWidget(search_box)
        
        # Scroll area for conversations
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("border: none;")
        
        self.conversations_widget = QWidget()
        self.conversations_layout = QVBoxLayout(self.conversations_widget)
        self.conversations_layout.setContentsMargins(5, 5, 5, 5)
        self.conversations_layout.setSpacing(5)
        self.conversations_layout.setAlignment(Qt.AlignTop)
        
        # Add placeholder for empty state
        self.empty_label = QLabel("No conversations yet.\n\nJoin a channel with /join or\nsend a message with /msg to start chatting!")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #8C8C8C; padding: 20px; font-size: 11pt;")
        self.conversations_layout.addWidget(self.empty_label)
        
        scroll_area.setWidget(self.conversations_widget)
        chat_layout.addWidget(scroll_area)
        
        return chat_content
    
    @pyqtSlot(int)
    def on_sidebar_tab_changed(self, index):
        if self._suppress_sidebar_tab_action:
            self._suppress_sidebar_tab_action = False # Reset flag immediately
            return

        if not self.send_command_func or not self.input_entry.isEnabled():
            # Not connected or ready to send commands
            return

        tab_text = self.sidebar.tabText(index)
        command_to_send = None

        if tab_text == "Channels":
            command_to_send = "/channels"
        elif tab_text == "Users":
            command_to_send = "/users"

        if command_to_send:
            # Ensure sidebar is visible if it was hidden
            if self.sidebar.width() == 0:
                self.sidebar.setFixedWidth(250) # Adjust width as needed

            print(f"Sidebar tab '{tab_text}' selected by user click, sending: {command_to_send}")
            self.send_command_func(command_to_send)
            self.add_recent_command(command_to_send)
    
    def update_input_placeholder(self):
        """Update input placeholder based on current conversation context"""
        if self.current_screen == "chat" and self.active_conversation:
            if self.active_conversation.startswith("channel:"):
                channel_name = self.active_conversation.split(':', 1)[1]
                self.input_entry.setPlaceholderText(f"Message #{channel_name}...")
            elif self.active_conversation.startswith("dm:"):
                username = self.active_conversation.split(':', 1)[1]
                if username == "You":
                    self.input_entry.setPlaceholderText("Message yourself...")
                else:
                    self.input_entry.setPlaceholderText(f"Message @{username}...")
        else:
            self.input_entry.setPlaceholderText("Type a message or command... (type /help for commands)")

    def switch_screen(self, screen_type):
        """Switch between main and chat screens"""
        self.current_screen = screen_type
        
        if screen_type == "main":
            self.main_screen_content.show()
            self.chat_screen_content.hide()
            self.main_screen_button.setChecked(True)
            self.chat_screen_button.setChecked(False)
            
            # Update header and clear active conversation
            self.chat_header_label.setText("Main Command Center")
            self.members_button.hide()
            self.hide_channel_members()
            self.active_conversation = None
            
            # Show main screen history
            self.refresh_main_screen()
            self.update_input_placeholder() 
            
        elif screen_type == "chat":
            self.main_screen_content.hide()
            self.chat_screen_content.show()
            self.main_screen_button.setChecked(False)
            self.chat_screen_button.setChecked(True)
            self.update_input_placeholder() 
            
            # If no active conversation, show instructions
            if not self.active_conversation:
                self.chat_header_label.setText("Select a Conversation")
                self.text_area.clear()
                self.text_area.append("<p style='color: #8C8C8C; text-align: center; margin-top: 50px;'><b>Select a conversation from the left to start chatting</b></p>")

    def refresh_main_screen(self):
        """Refresh the main screen with recent activity and show main history"""
        # Show main history in text area
        self.text_area.clear()
        self.text_area.append("<p style='color: #90EE90; font-weight: bold;'>📋 Main Command Center</p>")
        self.text_area.append("<p style='color: #87CEEB;'>Use this screen to run commands and manage your WireGuard chat session.</p>")
        self.text_area.append("<p style='color: #87CEEB;'>Switch to 'Chats' to view individual conversations.</p>")
        
        # Show main history
        for timestamp, message, tag in self.main_history:
            self.display_message_in_area(message, tag, timestamp, self.text_area)

    def execute_quick_command(self, command):
        """Execute a quick command from the main screen"""
        if self.send_command_func: # Check if send_command_func is available for sending commands
            if command == "/channels":
                # Show and switch to channels tab
                if self.sidebar.width() == 0:
                    self.sidebar.setFixedWidth(250)
                self._suppress_sidebar_tab_action = True # Suppress action in on_sidebar_tab_changed
                self.sidebar.setCurrentIndex(1)  # Switch to channels tab (index 1)
                self.send_command_func(command) # execute_quick_command still sends it
                self.add_recent_command(command)
            elif command == "/users":
                # Show and switch to users tab
                if self.sidebar.width() == 0:
                    self.sidebar.setFixedWidth(250)
                self._suppress_sidebar_tab_action = True # Suppress action
                self.sidebar.setCurrentIndex(0)  # Switch to users tab (index 0)
                self.send_command_func(command) # execute_quick_command still sends it
                self.add_recent_command(command)
            # For other commands not affecting these tabs, ensure they are still sent
            elif command not in ["/channels", "/users"]:
                self.send_command_func(command)
                self.add_recent_command(command)
        # Fallback for tab switching even if not fully connected to send commands
        # (e.g., send_command_func is None or not ready)
        # This allows the UI to respond by switching tabs at least.
        elif command == "/channels":
            if self.sidebar.width() == 0:
                self.sidebar.setFixedWidth(250)
            self._suppress_sidebar_tab_action = True # Still suppress if tab change happens
            self.sidebar.setCurrentIndex(1)
            self.add_recent_command(command) # Log the attempt
        elif command == "/users":
            if self.sidebar.width() == 0:
                self.sidebar.setFixedWidth(250)
            self._suppress_sidebar_tab_action = True # Still suppress if tab change happens
            self.sidebar.setCurrentIndex(0)
            self.add_recent_command(command) # Log the attempt

    def add_recent_command(self, command):
        """Add command to recent commands list"""
        # Avoid duplicates
        for i in range(self.recent_commands_list.count()):
            if self.recent_commands_list.item(i).text() == command:
                self.recent_commands_list.takeItem(i)
                break
        
        # Add to top
        self.recent_commands_list.insertItem(0, command)
        
        # Limit to 10 recent commands
        while self.recent_commands_list.count() > 10:
            self.recent_commands_list.takeItem(self.recent_commands_list.count() - 1)

    def toggle_sidebar(self):
        """Toggle the right sidebar (preserved from original)"""
        if self.sidebar.width() == 0:
            self.sidebar.setFixedWidth(250)
        else:
            self.sidebar.setFixedWidth(0)

    def handle_new_dm_conversation(self, conv_id, username, message):
        """Handle new DM conversation creation and notification"""
        try:
            # Create the conversation
            self.add_conversation(conv_id, username, is_channel=False)
            
            # If we're on main screen, show notification and auto-switch
            if self.current_screen == "main":
                # Show notification
                self.signals.message_received.emit(f"💬 New message from {username}", "info_highlight")
                
                # Auto-switch to chat screen and activate conversation
                self.switch_screen("chat")
                self.activate_conversation(conv_id)
            else:
                # If already on chat screen, just activate the conversation
                self.activate_conversation(conv_id)
                
        except Exception as e:
            print(f"Error handling new DM conversation: {e}")

    def init_channel_members_panel(self):
        # Create the channel members panel that will slide in when a channel is active
        self.channel_members_panel = QWidget()
        self.channel_members_panel.setFixedWidth(0)  # Hidden initially
        self.channel_members_panel.setStyleSheet("""
            background-color: #2A2A2A;
            border-left: 1px solid #3A3A3A;
        """)
        
        members_layout = QVBoxLayout(self.channel_members_panel)
        members_layout.setContentsMargins(0, 0, 0, 0)
        
        # Header
        members_header = QWidget()
        members_header.setFixedHeight(50)
        members_header.setStyleSheet("background-color: #323233;")
        members_header_layout = QHBoxLayout(members_header)
        members_header_layout.setContentsMargins(15, 0, 15, 0)
        
        self.members_header_label = QLabel("Channel Members")
        self.members_header_label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        members_header_layout.addWidget(self.members_header_label)
        
        # Close button
        close_button = QPushButton("×")
        close_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #B0B0B0;
                font-size: 18px;
                border: none;
                padding: 0;
                min-width: 20px;
                max-width: 20px;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        close_button.clicked.connect(self.hide_channel_members)
        members_header_layout.addWidget(close_button)
        
        members_layout.addWidget(members_header)
        
        # Members list
        self.members_list_widget = QListWidget()
        self.members_list_widget.setStyleSheet("""
            QListWidget {
                background-color: #2A2A2A;
                border: none;
                color: #D4D4D4;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #3A3A3A;
            }
            QListWidget::item:selected {
                background-color: #3A3A3A;
            }
        """)
        members_layout.addWidget(self.members_list_widget)
        
        # Add right-click menu
        self.members_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.members_list_widget.customContextMenuRequested.connect(self.show_member_menu)
        
        # Add to right panel layout
        self.right_panel_layout.addWidget(self.channel_members_panel)

    def connect_signals(self):
        self.send_button.clicked.connect(self.on_send_input)
        self.input_entry.returnPressed.connect(self.on_send_input)
        self.signals.message_received.connect(self.update_text_area)
        self.signals.status_updated.connect(self.update_status_bar)
        self.signals.connection_state_changed.connect(self.handle_connection_state)
        self.signals.title_updated.connect(self.setWindowTitle)
        self.signals.user_list_updated.connect(self.update_user_list)
        self.signals.channel_list_updated.connect(self.update_channel_list)
        
        # Connect conversation signals
        self.signals.conversation_joined.connect(self.handle_conversation_joined)
        self.signals.conversation_left.connect(self.handle_conversation_left)
        self.signals.channel_members_updated.connect(self.update_channel_members)
        
        # Preserved original double-click handlers
        self.user_list_widget.itemDoubleClicked.connect(self.on_user_double_clicked)
        self.channel_list_widget.itemDoubleClicked.connect(self.on_channel_double_clicked)

    def enhance_user_list_right_click(self):
        # Create a context menu for the user list (preserved from original)
        self.user_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.user_list_widget.customContextMenuRequested.connect(self.show_user_menu)
        
        # Also for channel list (preserved from original)
        self.channel_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.channel_list_widget.customContextMenuRequested.connect(self.show_channel_menu)
        
        # Add double-click handlers for recent commands
        self.recent_commands_list.itemDoubleClicked.connect(self.on_recent_command_double_clicked)

    @pyqtSlot('QListWidgetItem*')
    def on_recent_command_double_clicked(self, item):
        """Execute recent command when double-clicked"""
        command = item.text()
        if self.send_command_func:
            self.send_command_func(command)

    def show_user_menu(self, position):
        # Get the item at the position (preserved from original)
        item = self.user_list_widget.itemAt(position)
        if not item:
            return
            
        username = item.text()
        
        # Create the context menu
        menu = QMenu()
        message_action = menu.addAction(f"Message {username}")
        whois_action = menu.addAction(f"Whois {username}")
        
        # Show the menu and get the selected action
        action = menu.exec_(self.user_list_widget.mapToGlobal(position))
        
        # Handle the selected action
        if action == message_action:
            self.input_entry.setText(f"/msg {username} ")
            self.input_entry.setFocus()
            self.input_entry.setCursorPosition(len(self.input_entry.text()))
        elif action == whois_action:
            if self.send_command_func:
                self.send_command_func(f"/whois {username}")

    def show_channel_menu(self, position):
        # Get the item at the position (preserved from original)
        item = self.channel_list_widget.itemAt(position)
        if not item:
            return
            
        channel = item.text()
        
        # Create the context menu
        menu = QMenu()
        join_action = menu.addAction(f"Join #{channel}")
        info_action = menu.addAction(f"Channel info")
        
        # Show the menu and get the selected action
        action = menu.exec_(self.channel_list_widget.mapToGlobal(position))
        
        # Handle the selected action
        if action == join_action:
            if self.send_command_func:
                self.send_command_func(f"/join {channel}")
        elif action == info_action:
            if self.send_command_func:
                self.send_command_func(f"/info {channel}")

    def show_member_menu(self, position):
        # Right-click menu for channel members
        item = self.members_list_widget.itemAt(position)
        if not item:
            return
            
        username = item.text()
        
        menu = QMenu()
        message_action = menu.addAction(f"Message {username}")
        whois_action = menu.addAction(f"Whois {username}")
        
        action = menu.exec_(self.members_list_widget.mapToGlobal(position))
        
        if action == message_action:
            self.input_entry.setText(f"/msg {username} ")
            self.input_entry.setFocus()
            self.input_entry.setCursorPosition(len(self.input_entry.text()))
        elif action == whois_action:
            if self.send_command_func:
                self.send_command_func(f"/whois {username}")

    def display_message_in_area(self, message, tag, timestamp, text_area):
        """Helper method to display message in a text area"""
        try:
            if tag == "info_html":
                formatted_message = message
            else:
                escaped_message = html_escape(message).replace('\n', '<br>')
                escaped_message = re.sub(r'(https?://\S+)', r'<a href="\1">\1</a>', escaped_message)
                formatted_message = f"<span class='{tag}'>{escaped_message}</span>"
            
            timestamp_str = timestamp.strftime("%H:%M:%S") if isinstance(timestamp, datetime) else timestamp
            final_html = f"<p><span class='timestamp'>[{timestamp_str}]</span> {formatted_message}</p>"
            
            text_area.append(final_html)
            text_area.verticalScrollBar().setValue(text_area.verticalScrollBar().maximum())
        except Exception as e:
            print(f"Error displaying message: {e}")
            text_area.append(f"<p>{html_escape(str(message))}</p>")

    @pyqtSlot(str, str)
    def update_text_area(self, message, tag):
        """Smart message routing: Route messages appropriately based on current screen and message type"""
        try:
            timestamp = datetime.now()

            # FILTER OUT: Don't show channel/user list responses in main text area
            # These will be handled by the sidebar panels instead
            if ("Available Channels:" in message or 
                ("Showing " in message and "users" in message) or
                (tag == "info_success" and "Server OK:" in message)):
                return
            
            # ADD THIS SECTION: Handle channel creation success and auto-open chat
            if tag == "info_highlight" and "Channel '" in message and "' created." in message:
                # Extract channel name from success message
                match = re.search(r"Channel '([^']+)' created\.", message)
                if match:
                    channel_name = match.group(1)
                    print(f"DEBUG: Auto-opening created channel: {channel_name}")
                    
                    # Create the conversation immediately
                    conv_id = f"channel:{channel_name}"
                    self.add_conversation(conv_id, channel_name, is_channel=True)
                    
                    # Switch to chat screen and activate the conversation
                    self.switch_screen("chat")
                    self.activate_conversation(conv_id)
                    
                    # Update input placeholder so they know they can type
                    self.update_input_placeholder()
                    
                    

            
            # CRITICAL: Always show certain message types in main screen regardless of routing
            critical_tags = ["error", "error_critical", "info_highlight", "info_success", "server"]
            force_main_display = tag in critical_tags or message.startswith("GUI") or "Connected" in message or "Server:" in message
            
            # Check if this is a conversation message
            is_conversation_message = False
            conv_id = None
            parsed_dm_sender_username = None 
            
            if tag == "channel":
                match = re.match(r'\[(.*?)\] <(.*?)>:', message)
                if match:
                    channel_name = match.group(1)
                    sender_username = match.group(2)
                    
                    # Skip our own echoed messages from server
                    if (hasattr(self, '_protocol_instance') and self._protocol_instance and 
                        hasattr(self._protocol_instance, 'chat_username') and 
                        sender_username == self._protocol_instance.chat_username):
                        print(f"DEBUG: Skipping server echo for own message in {channel_name}")
                        return
                    
                    # Process messages from other users normally
                    conv_id = f"channel:{channel_name}"
                    is_conversation_message = True
                else:
                    # Fallback for messages that don't match expected format
                    print(f"DEBUG: Channel message format not recognized: '{message}'")
                    # Try the original simple regex as fallback
                    match = re.match(r'\[(.*?)\]', message)
                    if match:
                        channel_name = match.group(1)
                        conv_id = f"channel:{channel_name}"
                        is_conversation_message = True

                        #Error
            elif tag == "dm":
                # Priority 1: Incoming DM from another user: "[From SenderName]: message_text"
                # This format comes from client_logic.py USER_MESSAGE_RESPONSE
                match_incoming = re.match(r'\[(?:From|PM from) (.*?)\]:', message)
                if match_incoming:
                    username_dm = match_incoming.group(1) # Renamed to avoid conflict
                    if username_dm == "You": # This is a self-message like "[From You]: ..."
                        conv_id = "dm:You"
                    else:
                        conv_id = f"dm:{username_dm}"
                        # This is an incoming DM from another user, store their name
                        parsed_dm_sender_username = username_dm 
                    is_conversation_message = True
                # Priority 2: Outgoing DM echo or PM to another user: "[You → Receiver]: ..." or "[PM to Receiver]: ..."
                # These formats are typically generated by the GUI itself for optimistic display or server confirmation.
                elif "[You → " in message or "[PM to " in message: # Check if it's an outgoing message confirmation
                    match_outgoing = re.match(r'\[(?:You →|PM to) (.*?)\]:', message)
                    if match_outgoing:
                        username_dm = match_outgoing.group(1)
                        conv_id = f"dm:{username_dm}" # This will be normalized to dm:You later if username_dm is self
                        is_conversation_message = True
                # Priority 3: Specific self-chat format "[You → You]:" (might be redundant if above handles "dm:You" correctly)
                elif "[You → You]:" in message : # This is explicitly a self-message
                     conv_id = "dm:You"
                     is_conversation_message = True


            #END OF FIX
            elif tag == "own_message":

                print(f"DEBUG: Received own_message from server: {message}")
                
                
                pass  # Let the message continue to be processed normally



            # If parsed_dm_sender_username is set, it means we've identified an incoming DM from another specific user.
            if parsed_dm_sender_username and conv_id: # conv_id would be like "dm:sender_name"
                # This will:
                # 1. Create the ConversationItem widget if it doesn't exist (via add_conversation).
                # 2. If on "main" screen, it shows a notification, switches to "chat" screen, and activates the conversation.
                # 3. If on "chat" screen but a *different* conversation is active, it activates this one.
                # 4. If already on "chat" screen and this conversation is active, activate_conversation does minimal work.
                self.handle_new_dm_conversation(conv_id, parsed_dm_sender_username, message)

            #END OF FIX

            # NORMALIZE self-messaging conv_ids to "dm:You"
            if is_conversation_message and conv_id and conv_id.startswith("dm:"):
                if hasattr(self, '_protocol_instance'):
                    protocol = self._protocol_instance
                    if protocol and hasattr(protocol, 'chat_username'):
                        username = conv_id.split(':', 1)[1]
                        if username == protocol.chat_username:
                            conv_id = "dm:You"
            
            # Route to conversation if it's a conversation message
            if is_conversation_message and conv_id:
                self.route_message_to_conversation(conv_id, message, tag, timestamp)
                
                # Display logic based on current screen and active conversation
                if self.current_screen == "chat" and self.active_conversation == conv_id:
                    # Show in text area if we're viewing this conversation
                    self.display_message_in_area(message, tag, timestamp, self.text_area)
                elif force_main_display:
                    # Always show critical messages even if they're conversation messages
                    if self.current_screen == "main":
                        self.main_history.append((timestamp, message, tag))
                        self.display_message_in_area(message, tag, timestamp, self.text_area)
                # Otherwise, don't show conversation messages in main screen to keep it clean
                
            else:
                # Non-conversation message - add to main history and show if on main screen
                self.main_history.append((timestamp, message, tag))
                
                # Limit main history size
                if len(self.main_history) > 500:
                    self.main_history = self.main_history[-400:]
                
                # Show on main screen if that's where we are, or if it's critical
                if self.current_screen == "main" or force_main_display:
                    self.display_message_in_area(message, tag, timestamp, self.text_area)
                
                # Add to recent commands if it's a command result
                if message.startswith("/"):
                    self.add_recent_command(message.split()[0] if message.split() else message)
                    
        except Exception as e:
            print(f"Error in update_text_area: {e}")
            # Fallback - always show critical errors
            self.text_area.append(f"<p style='color: #FF6347;'>{html_escape(str(message))}</p>")
            
        #END OF PART
    
    def route_message_to_conversation(self, conv_id, message, tag, timestamp):
        """Route message to conversation"""
        try:
            # NORMALIZE: All self-messaging goes to "dm:You" regardless of username
            original_conv_id = conv_id
            if conv_id.startswith("dm:") and hasattr(self, '_protocol_instance'):
                protocol = self._protocol_instance
                if protocol and hasattr(protocol, 'chat_username'):
                    username = conv_id.split(':', 1)[1]
                    if username == protocol.chat_username:
                        conv_id = "dm:You"
            
            # If we normalized and the old conversation exists, merge it
            if original_conv_id != conv_id and original_conv_id in self.conversations:
                print(f"Merging conversation {original_conv_id} into {conv_id}")
                # Remove the old conversation
                old_conversation = self.conversations[original_conv_id]
                self.conversations_layout.removeWidget(old_conversation)
                old_conversation.deleteLater()
                del self.conversations[original_conv_id]
                
                # Merge history if it exists
                if original_conv_id in self.conversation_history:
                    if conv_id not in self.conversation_history:
                        self.conversation_history[conv_id] = []
                    self.conversation_history[conv_id].extend(self.conversation_history[original_conv_id])
                    del self.conversation_history[original_conv_id]
            
            # Create conversation item if it doesn't exist
            if conv_id not in self.conversations:
                name = conv_id.split(':', 1)[1]
                is_channel = conv_id.startswith("channel:")
                print(f"Creating conversation for incoming message: {name}")
                self.add_conversation(conv_id, name, is_channel)
                
                # Remove empty label if it was showing
                if hasattr(self, 'empty_label') and self.empty_label.isVisible():
                    self.empty_label.hide()
            
            # Add to history
            if conv_id not in self.conversation_history:
                self.conversation_history[conv_id] = []
            self.conversation_history[conv_id].append((timestamp, message, tag))
            
            # Limit conversation history size
            if len(self.conversation_history[conv_id]) > 200:
                self.conversation_history[conv_id] = self.conversation_history[conv_id][-150:]
            
            # Update conversation preview
            if conv_id in self.conversations:
                conversation = self.conversations[conv_id]
                try:
                    conversation.update_preview(message)
                except Exception as e:
                    print(f"Error updating preview: {e}")
                    conversation.preview_label.setText("New message")
                
                # If this isn't the active conversation, increment unread count
                if self.active_conversation != conv_id or self.current_screen != "chat":
                    conversation.increment_unread()
        except Exception as e:
            print(f"Error in route_message_to_conversation: {e}")


    
    #END OF FIX AREA
        
    def add_conversation(self, conv_id, name, is_channel=False):
        try:
            # Check if conversation already exists to avoid duplicates
            if conv_id in self.conversations:
                return self.conversations[conv_id]
            
            # Create and add the conversation item
            conversation = ConversationItem(conv_id, name, is_channel)
            conversation.clicked.connect(self.activate_conversation)
            
            # Remove empty label if it's there
            if self.empty_label.isVisible():
                self.empty_label.hide()
            
            # Add to layout and track
            self.conversations_layout.addWidget(conversation)
            self.conversations[conv_id] = conversation
            
            # Initialize history if it doesn't exist
            if conv_id not in self.conversation_history:
                self.conversation_history[conv_id] = []
            
            return conversation
        except Exception as e:
            print(f"Error adding conversation: {e}")
            # Return a dummy conversation item that won't crash
            dummy = ConversationItem(conv_id, name, is_channel)
            dummy.clicked.connect(lambda: None)  # No-op to prevent crashes
            return dummy
    
    def activate_conversation(self, conv_id):
        """Switch to chat screen and activate conversation"""
        try:
            # Switch to chat screen
            self.switch_screen("chat")
            
            # Deselect previous conversation
            if self.active_conversation and self.active_conversation in self.conversations:
                try:
                    self.conversations[self.active_conversation].set_selected(False)
                except Exception as e:
                    print(f"Error deselecting previous conversation: {e}")
            
            # Select new conversation
            self.active_conversation = conv_id
            if conv_id in self.conversations:
                try:
                    conversation = self.conversations[conv_id]
                    conversation.set_selected(True)
                    conversation.clear_unread()
                    
                    # Update header
                    name = conv_id.split(':', 1)[1]
                    is_channel = conv_id.startswith("channel:")
                    prefix = "#" if is_channel else "@"
                    self.chat_header_label.setText(f"{prefix}{name}")
                    
                    # Show/hide members button for channels
                    if is_channel:
                        self.members_button.show()
                        channel_name = name
                        
                        # Request updated channel info when activating a channel
                        if self.send_command_func:
                            try:
                                self.send_command_func(f"/info {channel_name}")
                            except Exception as e:
                                print(f"Error requesting channel info: {e}")
                        
                        # Show existing members while waiting for the update
                        if channel_name in self.channel_members:
                            try:
                                self.show_channel_members(channel_name)
                            except Exception as e:
                                print(f"Error showing channel members: {e}")
                    else:
                        self.members_button.hide()
                        self.hide_channel_members()
                    
                    # Show conversation history
                    try:
                        self.text_area.clear()
                        if conv_id in self.conversation_history:
                            for timestamp, msg_text, msg_tag in self.conversation_history[conv_id]:
                                self.display_message_in_area(msg_text, msg_tag, timestamp, self.text_area)
                    except Exception as e:
                        print(f"Error refreshing conversation history: {e}")
                except Exception as e:
                    print(f"Error setting up conversation: {e}")
            self.update_input_placeholder()
        except Exception as e:
            print(f"Critical error in activate_conversation: {e}")

    def export_current_history(self):
        """Export history for current view (main or conversation)"""
        try:
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if self.current_screen == "main":
                # Export main screen history
                filename = f"wg_chat_main_history_{timestamp_str}.txt"
                history = self.main_history
                title = "Main Command Center History"
            elif self.active_conversation:
                # Export active conversation history
                conv_name = self.active_conversation.split(':', 1)[1]
                conv_type = "channel" if self.active_conversation.startswith("channel:") else "dm"
                filename = f"wg_chat_{conv_type}_{conv_name}_{timestamp_str}.txt"
                history = self.conversation_history.get(self.active_conversation, [])
                title = f"{'Channel' if conv_type == 'channel' else 'Direct Message'}: {conv_name}"
            else:
                self.signals.message_received.emit("No active conversation to export.", "error")
                return
            
            # Ensure the filename is safe
            filename = "".join(c for c in filename if c.isalnum() or c in "._-")
            
            # Create directory if it doesn't exist
            export_dir = "chat_exports"
            os.makedirs(export_dir, exist_ok=True)
            filepath = os.path.join(export_dir, filename)
            
            # Write history to file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"WireGuard Chat Export\n")
                f.write(f"Title: {title}\n")
                f.write(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 50 + "\n\n")
                
                if not history:
                    f.write("No messages found.\n")
                else:
                    for timestamp, message, tag in history:
                        # Clean the message of HTML tags for plain text export
                        clean_message = re.sub(r'<[^>]+>', '', message)
                        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S") if isinstance(timestamp, datetime) else str(timestamp)
                        f.write(f"[{timestamp_str}] {clean_message}\n")
            
            self.signals.message_received.emit(f"History exported to: {filepath}", "info_success")
            
        except Exception as e:
            print(f"Error exporting history: {e}")
            self.signals.message_received.emit(f"Error exporting history: {e}", "error")

    @pyqtSlot(str, str)
    def handle_conversation_joined(self, conv_id, name):
        # Create conversation if it doesn't exist
        if conv_id not in self.conversations:
            is_channel = conv_id.startswith("channel:")
            self.add_conversation(conv_id, name, is_channel)
        
        # Activate the conversation
        self.activate_conversation(conv_id)
    
    @pyqtSlot(str)
    def handle_conversation_left(self, conv_id):
        # Remove conversation
        if conv_id in self.conversations:
            conversation = self.conversations[conv_id]
            self.conversations_layout.removeWidget(conversation)
            conversation.deleteLater()
            del self.conversations[conv_id]
            
            # Clear conversation history
            if conv_id in self.conversation_history:
                del self.conversation_history[conv_id]
            
            # Clear active conversation if it was this one
            if self.active_conversation == conv_id:
                self.switch_screen("main")
            
            # Show empty label if no conversations left
            if not self.conversations:
                self.empty_label.show()

    @pyqtSlot(str)
    def update_status_bar(self, status_text):
        self.status_bar.showMessage(status_text)

    @pyqtSlot(list)
    def update_user_list(self, users):
        """Update user list (preserved from original functionality)"""
        self.user_list_widget.clear()
        
        # Get the protocol instance to check current username
        protocol = None
        if hasattr(self, '_protocol_instance'):
            protocol = self._protocol_instance
        
        for username in sorted(users, key=str.lower):
            item = QListWidgetItem(username)
            
            # Highlight current user
            if protocol and hasattr(protocol, 'chat_username') and protocol.chat_username == username:
                font = QFont()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QBrush(QColor("#007ACC")))
                item.setForeground(QBrush(QColor("#FFFFFF")))
            
            self.user_list_widget.addItem(item)
        
        # Expand the sidebar if it was previously hidden
        if self.sidebar.width() == 0 and users:
            self.sidebar.setFixedWidth(250)
        self.sidebar.setCurrentIndex(0) # Switch to users tab (index 0)

    @pyqtSlot(list)
    def update_channel_list(self, channels):
        """Update channel list (preserved from original functionality)"""
        self.channel_list_widget.clear()
        self.channel_list_widget.addItems(sorted(channels, key=str.lower))
        
        # Expand the sidebar if it was previously hidden
        if self.sidebar.width() == 0 and channels:
            self.sidebar.setFixedWidth(250)
        self.sidebar.setCurrentIndex(1) # Switch to channels tab (index 1)

    @pyqtSlot(str, list)
    def update_channel_members(self, channel_name, members):
        try:
            if not channel_name:
                return
                
            # Store channel members
            self.channel_members[channel_name] = members
            
            # Check if this is the active channel
            if not self.active_conversation:
                return
                
            active_channel = None
            if self.active_conversation.startswith("channel:"):
                active_channel = self.active_conversation.split(':', 1)[1]
            
            if active_channel == channel_name:
                self.show_channel_members(channel_name)
        except Exception as e:
            print(f"Error updating channel members: {e}")

    def show_channel_members(self, channel_name=None):
        try:
            # If no channel specified, use active conversation
            if not channel_name and self.active_conversation and self.active_conversation.startswith("channel:"):
                channel_name = self.active_conversation.split(':', 1)[1]
            
            if not channel_name:
                return
            
            # Check if we have members for this channel
            members = self.channel_members.get(channel_name, [])
            
            # Update header
            self.members_header_label.setText(f"#{channel_name} Members")
            
            # Update list
            self.members_list_widget.clear()
            for member in sorted(members, key=str.lower):
                item = QListWidgetItem(member)
                
                # Bold for current user
                protocol = self._protocol_instance if hasattr(self, '_protocol_instance') else None
                if protocol and hasattr(protocol, 'chat_username') and protocol.chat_username == member:
                    font = QFont()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QBrush(QColor("#90EE90")))  # Light green
                
                self.members_list_widget.addItem(item)
            
            # Show panel if needed
            if self.channel_members_panel.width() == 0:
                self.channel_members_panel.setFixedWidth(200)
        except Exception as e:
            print(f"Error showing channel members: {e}")

    def hide_channel_members(self):
        self.channel_members_panel.setFixedWidth(0)

    @pyqtSlot(str, dict)
    def handle_connection_state(self, state, data):
        """Handle connection state changes (preserved from original)"""
        if state == "connected":
            username = data.get('username', '???')
            session = data.get('session', '???')
            server_welcome = data.get('message', '')
            self.signals.status_updated.emit(f"Connected as {username} (Session: {session})")
            self.signals.title_updated.emit(f"WG Chat - {username}@{SERVER_HOST}")
            self.update_text_area(f"Successfully connected to {SERVER_HOST}!", "info_highlight")
            if server_welcome:
                self.update_text_area(f"Server: {server_welcome}", "server")
            self.update_text_area("Use the Main screen for commands or switch to Chats for conversations.", "info")
            self.input_entry.setEnabled(True)
            self.send_button.setEnabled(True)
            self.input_entry.setFocus()
        elif state == "disconnected":
            self.signals.status_updated.emit("Disconnected")
            self.update_text_area("Connection lost or closed.", "error_critical")
            self.input_entry.setEnabled(False)
            self.send_button.setEnabled(False)
        elif state == "shutdown":
            self.signals.status_updated.emit("Server Shutdown")
            self.update_text_area(f"SERVER SHUTDOWN: {html_escape(data.get('message', 'No reason given.'))}", "error_critical")
            self.input_entry.setEnabled(False)
            self.send_button.setEnabled(False)
            if not self.shutdown_event.is_set():
                self.shutdown_event.set()

    @pyqtSlot()
    def on_send_input(self):
        """Handle input sending with context-aware messaging"""
        message_to_send = self.input_entry.text().strip()
        if not message_to_send:
            return

        # Handle /history command specially
        if message_to_send == "/history":
            self.export_current_history()
            self.input_entry.clear()
            return
        
        # CONTEXT-AWARE MESSAGING: If in a conversation and not a command, auto-format
        # original_input_for_log = message_to_send # This was added, good for debugging
        if not message_to_send.startswith("/") and self.current_screen == "chat" and self.active_conversation:
            if self.active_conversation.startswith("channel:"):
                channel_name = self.active_conversation.split(':', 1)[1]
                message_to_send = f"/say {channel_name} {message_to_send}"
                print(f"Auto-formatted for channel: {message_to_send}")
            elif self.active_conversation.startswith("dm:"):
                username_part = self.active_conversation.split(':', 1)[1]
                actual_target_username = username_part
                if username_part == "You":
                    if hasattr(self, '_protocol_instance') and self._protocol_instance and \
                       hasattr(self._protocol_instance, 'chat_username') and self._protocol_instance.chat_username:
                        actual_target_username = self._protocol_instance.chat_username
                    else: 
                        actual_target_username = "You" 
                message_to_send = f"/msg {actual_target_username} {message_to_send}"
                print(f"Auto-formatted for DM: {message_to_send}")
        
        # Handle /channels and /users commands specially for sidebar interaction
        if message_to_send == "/channels" or message_to_send.startswith("/channels "):
            if self.send_command_func:
                if self.sidebar.width() == 0: self.sidebar.setFixedWidth(250)
                self._suppress_sidebar_tab_action = True
                self.sidebar.setCurrentIndex(1) 
                self.add_recent_command(message_to_send.split(" ",1)[0]) # AFTER: Logs base command
                self.send_command_func(message_to_send)
            self.input_entry.clear()
            return
        
        if message_to_send == "/users" or message_to_send.startswith("/users "):
            if self.send_command_func:
                if self.sidebar.width() == 0: self.sidebar.setFixedWidth(250)
                self._suppress_sidebar_tab_action = True
                self.sidebar.setCurrentIndex(0) 
                self.add_recent_command(message_to_send.split(" ",1)[0]) # AFTER: Logs base command
                self.send_command_func(message_to_send)
            self.input_entry.clear()
            return
        
        # If send_command_func is not set, we can't proceed
        if not self.send_command_func: # Added check for send_command_func earlier
            self.update_text_area("Error: Send function not ready (not connected?).", "error")
            self.input_entry.clear() 
            return

        # Log command if it's a command
        if message_to_send.startswith("/"):
            self.add_recent_command(message_to_send.split(" ", 1)[0])
        
        if message_to_send.startswith("/say "):
            parts = message_to_send.split(" ", 2)
            if len(parts) >= 3:
                channel_name = parts[1]
                actual_message_text = parts[2]
                conv_id = f"channel:{channel_name}"

                # Perform optimistic display ONLY if the conversation already exists in the GUI.
                if conv_id in self.conversations: # AFTER: Optimistic UI is conditional
                    timestamp = datetime.now()
                    username_display = "You" 
                    if hasattr(self, '_protocol_instance') and self._protocol_instance and \
                       hasattr(self._protocol_instance, 'chat_username') and self._protocol_instance.chat_username:
                        username_display = self._protocol_instance.chat_username
                    
                    display_message = f"[{channel_name}] <{username_display}>: {actual_message_text}"
                    
                    self.route_message_to_conversation(conv_id, display_message, "own_message", timestamp)
                    if self.current_screen == "chat" and self.active_conversation == conv_id:
                        self.display_message_in_area(display_message, "own_message", timestamp, self.text_area)
        
        elif message_to_send.startswith("/msg "):
            parts = message_to_send.split(" ", 2)
            if len(parts) >= 3:
                to_username_cmd = parts[1] 
                actual_message_text = parts[2]

                own_username_internal = None
                if hasattr(self, '_protocol_instance') and self._protocol_instance and \
                   hasattr(self._protocol_instance, 'chat_username'):
                    own_username_internal = self._protocol_instance.chat_username

                if own_username_internal and to_username_cmd == own_username_internal:
                    conv_id = "dm:You"
                else:
                    conv_id = f"dm:{to_username_cmd}"
                
                # Perform optimistic display ONLY if the conversation already exists.
                if conv_id in self.conversations: # AFTER: Optimistic UI is conditional
                    timestamp = datetime.now()
                    sender_display_name_opt = "You" 

                    if conv_id == "dm:You": 
                        display_message = f"[{sender_display_name_opt} → You]: {actual_message_text}"
                    else: 
                        display_message = f"[{sender_display_name_opt} → {to_username_cmd}]: {actual_message_text}"
                    
                    self.route_message_to_conversation(conv_id, display_message, "own_message", timestamp)
                    if self.current_screen == "chat" and self.active_conversation == conv_id:
                        self.display_message_in_area(display_message, "own_message", timestamp, self.text_area)

        # Actually send the command/message
        print(f"GUI: Sending to protocol: {message_to_send}")
        self.send_command_func(message_to_send)
        self.input_entry.clear()


    @pyqtSlot('QListWidgetItem*')
    def on_user_double_clicked(self, item):
        """Handle user double-click (preserved from original)"""
        username = item.text()
        
        # NORMALIZE: Self-messaging always goes to "dm:You"
        if hasattr(self, '_protocol_instance') and self._protocol_instance:
            protocol = self._protocol_instance
            if hasattr(protocol, 'chat_username') and username == protocol.chat_username:
                conv_id = "dm:You"
                display_name = "You"
            else:
                conv_id = f"dm:{username}"
                display_name = username
        else:
            conv_id = f"dm:{username}"
            display_name = username
        
        # Create conversation if it doesn't exist
        if conv_id not in self.conversations:
            self.signals.conversation_joined.emit(conv_id, display_name)
        else:
            self.activate_conversation(conv_id)
        
        # Focus input for messaging
        if display_name == "You":
            self.input_entry.setText(f"/msg {username} ")  # Use actual username for command
        else:
            self.input_entry.setText(f"/msg {username} ")
        self.input_entry.setFocus()
        self.input_entry.setCursorPosition(len(self.input_entry.text()))

    @pyqtSlot('QListWidgetItem*')
    def on_channel_double_clicked(self, item):
        """Handle channel double-click (preserved from original)"""
        channel_name = item.text()
        self.input_entry.setText(f"/join {channel_name}")
        self.input_entry.setFocus()
        self.input_entry.setCursorPosition(len(self.input_entry.text()))

    def closeEvent(self, event):
        """Handle window close (preserved from original)"""
        if not self.shutdown_event.is_set():
            reply = QMessageBox.question(self, 'Confirm Exit', "Are you sure you want to quit?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.update_text_area("Disconnecting...", "info")
                self.shutdown_event.set()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# Extended client_logic to handle conversation management properly (preserved from original)
def extend_client_logic(protocol):
    original_handle_chat_protocol = protocol.handle_chat_protocol_message
    
    # Import constants directly
    from client_logic import (
        CHANNEL_JOIN_RESPONSE, CHANNEL_LEFT_RESPONSE, 
        CHANNEL_INFO_RESPONSE, USER_LIST_RESPONSE, CHANNEL_MESSAGE_RESPONSE
    )
    
    def extended_handle_chat_protocol(msg):
        res_type = msg.get('response_type')
        response_handle = msg.get('response_handle')
        
        # Detect channel joins and leaves for conversation management
        if res_type == CHANNEL_JOIN_RESPONSE:
            channel_name = msg.get('channel', 'unknown')
            if msg.get('response_handle'):  # Our own join
                protocol.signals.conversation_joined.emit(f"channel:{channel_name}", channel_name)
                
                # Extract member list if available
                members = []
                if isinstance(msg.get('members'), list):
                    members = msg.get('members')
                elif isinstance(msg.get('info'), dict) and isinstance(msg.get('info').get('members'), list):
                    members = msg.get('info').get('members')
                
                if members:
                    protocol.signals.channel_members_updated.emit(channel_name, members)
        
        # Detect channel leave
        elif res_type == CHANNEL_LEFT_RESPONSE:
            channel_name = msg.get('channel', 'unknown')
            if msg.get('response_handle'):  # Our own leave
                protocol.signals.conversation_left.emit(f"channel:{channel_name}")
        
        # Extract channel info
        elif res_type == CHANNEL_INFO_RESPONSE:
            channel_name = msg.get('channel', 'unknown')
            members = msg.get('members', [])
            if isinstance(members, list):
                protocol.signals.channel_members_updated.emit(channel_name, members)
        
        # Continue with original handler - this is crucial for message display
        return original_handle_chat_protocol(msg)
    
    protocol.handle_chat_protocol_message = extended_handle_chat_protocol
    return protocol

async def watch_shutdown(shutdown_event: asyncio.Event, app: QApplication):
    """Watch for shutdown event (preserved from original)"""
    await shutdown_event.wait()
    print("Shutdown event detected by watcher, quitting application.")
    app.quit()

async def main_async(app: QApplication, window: ChatWindow, shutdown_event: asyncio.Event):
    """Main async function (preserved from original)"""
    loop = asyncio.get_running_loop()
    protocol_instance, transport_instance = None, None

    def _send_command_to_protocol(command_text):
        if protocol_instance:
            parse_and_send_command(command_text, protocol_instance)
        else:
            window.signals.message_received.emit("Error: Protocol not available.", "error")
    window.send_command_func = _send_command_to_protocol

    try:
        print(f"Attempting UDP connection to {SERVER_HOST}:{SERVER_PORT}")
        transport_instance, protocol_instance = await loop.create_datagram_endpoint(
            lambda: ChatClientProtocol(loop, window.signals, shutdown_event),
            remote_addr=(SERVER_HOST, SERVER_PORT))
        
        # Save protocol instance and extend with conversation handling
        window._protocol_instance = extend_client_logic(protocol_instance)
        
        print("Datagram endpoint created successfully.")
        window.signals.status_updated.emit(f"Connecting to {SERVER_HOST}:{SERVER_PORT}...")
    except OSError as e:
        window.signals.message_received.emit(f"Network Connection Error: {e}", "error_critical")
        window.signals.status_updated.emit("Connection Failed")
        shutdown_event.set()
    except Exception as e:
        window.signals.message_received.emit(f"Connection setup error: {e}", "error_critical")
        window.signals.status_updated.emit("Error")
        shutdown_event.set()
        traceback.print_exc()

    ping_task = asyncio.create_task(send_pings(protocol_instance)) if protocol_instance else None
    shutdown_watcher_task = asyncio.create_task(watch_shutdown(shutdown_event, app))
    
    await shutdown_event.wait()
    print("Shutdown signalled. Initiating asyncio cleanup...")
    active_tasks = [t for t in [ping_task, shutdown_watcher_task] if t and not t.done()]
    if active_tasks:
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*active_tasks, return_exceptions=True)
        print("Background tasks cancelled.")

    if protocol_instance and protocol_instance.wg_handshake_complete and protocol_instance.chat_session_id and transport_instance and not transport_instance.is_closing():
        print("GUI: Sending CHAT DISCONNECT via Wireguard...")
        protocol_instance.send_chat_message_via_wg({'request_type': DISCONNECT})
        await asyncio.sleep(0.1)
    if transport_instance and not transport_instance.is_closing():
        transport_instance.close()
    print("Asyncio cleanup finished.")

if __name__ == "__main__":
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        loop = asyncqt.QEventLoop(app)
        asyncio.set_event_loop(loop)
        shutdown_event = asyncio.Event()
        window = ChatWindow(loop, shutdown_event)
        window.show()
        loop.create_task(main_async(app, window, shutdown_event))
        exit_code = loop.run_forever()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt. Exiting.")
    except ImportError:
        sys.exit(1)
    except Exception as e:
        print(f"\nFATAL ERROR in GUI main execution: {e}")
        traceback.print_exc()
        if 'app' in locals() and app is not None:
            QMessageBox.critical(None, "Fatal Error", f"A fatal error occurred:\n{e}\n\nSee console for details.")
        sys.exit(1)