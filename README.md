# Secure WireGuard Chat Application

A secure, encrypted chat application implementing WireGuard protocols for end-to-end encrypted communication. Built as part of a University of Cape Town computer science project.

## Authors
- **Kabelo Mbayi** (MBYKAB002)
- **Siyanda Makhathini** (MKHSIY057) 
- **Simphiwe Mkhize** (MKHSIM067)

*University of Cape Town - May 31, 2025*

## Overview

This application provides secure real-time messaging using WireGuard's robust encryption protocols. It features a modern GUI with support for channels, direct messaging, and comprehensive user management, all protected by state-of-the-art cryptographic implementations.

## Features

### 🔐 Security & Encryption
- **Curve25519** elliptic curve Diffie-Hellman key exchange
- **ChaCha20-Poly1305** authenticated encryption for all messages
- **BLAKE2s** cryptographic hashing for protocol integrity
- **HKDF-based** key derivation functions
- **TAI64N** timestamp format for handshake security
- Private keys stored securely in separate files

### 💬 Communication Features
- **Multi-channel support** - Create and join encrypted channels
- **Direct messaging** - Private encrypted conversations
- **Real-time messaging** - Instant message delivery
- **User presence** - See who's online
- **Message history** - Export conversation histories

### 🖥️ User Interface
- **Dual-screen interface** - Toggle between Main (command center) and Chats
- **Sidebar navigation** - Quick access to users and channels
- **Context menus** - Right-click actions for users and channels
- **Quick actions** - One-click buttons for common tasks
- **Search functionality** - Find conversations easily

## Technical Implementation

### Cryptographic Primitives
The application implements core WireGuard cryptographic functions:

- `DH_GENERATE()` - Generates Curve25519 key pairs
- `DH()` - Performs Diffie-Hellman key exchange
- `AEAD()` - ChaCha20-Poly1305 encryption with authentication
- `AEAD_decrypt()` - Authenticated decryption
- `HASH()` - BLAKE2s cryptographic hashing
- `HMAC_hash()` - HMAC using BLAKE2s
- `MAC()` - Keyed BLAKE2s MAC generation
- `TAI64N()` - Secure timestamp generation

### Architecture
- **`wg_crypto.py`** - Core cryptographic primitives
- **`client_logic.py`** - Handshake and packet transport logic
- **GUI Components** - Modern interface with sidebar navigation

## Installation

```bash
# Clone the repository
git clone [repository-url]
cd secure-wireguard-chat

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

## Usage

### Basic Commands

#### Channel Management
```bash
/create channel_name [optional description]    # Create a new channel
/join channel_name                            # Join existing channel
/leave channel_name                          # Leave a channel
/info channel_name                           # Get channel information
```

#### Direct Messaging
```bash
/msg username message_text                   # Send direct message
# Or double-click username in Users list
# Or right-click username → "Message username"
```

#### User Management
```bash
/setuser new_username                        # Change your username
/whois username                             # Get user information
/users [offset]                             # List online users
/channels [offset]                          # List available channels
```

#### Utility Commands
```bash
/help                                       # Show available commands
/history                                    # Export chat history
/quit                                       # Exit application
```

### Interface Navigation

#### Screen Modes
- **Main Screen**: Command center with quick actions and recent commands
- **Chats Screen**: Conversation list and active chat view

#### Quick Actions (Main Screen)
- **List Users** - Populate sidebar with online users
- **List Channels** - Show available channels in sidebar
- **Who Am I** - Display your user information
- **Help** - Show command reference

#### Sidebar Features
- **Users Tab** - Online user list with context menus
- **Channels Tab** - Available channels with join options
- **Toggle Button** (≡) - Show/hide sidebar

### Security Features

#### Automatic Encryption
All communications are automatically encrypted using:
- Ephemeral key generation for each session
- Authenticated encryption for all message types
- Secure handshake protocol implementation
- Message authentication to prevent tampering

#### Key Management
- Private keys stored separately from application
- Automatic key rotation during handshakes
- Secure key derivation for session keys

## Example Usage

### Creating and Joining a Channel
1. Create a channel: `/create project-updates Important project news`
2. Join existing channel: `/join general`
3. View channel members: Click "Members" button in chat header
4. Send messages: Type directly when channel is selected

### Direct Messaging
1. Method 1: `/msg Alice Hello, how are you?`
2. Method 2: Double-click "Alice" in Users sidebar
3. Method 3: Right-click "Alice" → "Message Alice"

### Getting Information
```bash
/whois Alice
# Output:
# Whois: Alice
# Status: Active Session ID: 123456789
# Channels: N/A Transport: Wireguard
# WireGuard Public Key: [public_key_here]
```

## Security Considerations

- All messages are encrypted end-to-end using WireGuard protocols
- Private keys are never transmitted over the network
- Authentication prevents message tampering
- Forward secrecy through ephemeral key exchange
- Timestamps prevent replay attacks

## Data Management

- **Export History**: Save conversations to `.txt` files in `chat_exports/` folder
- **Search**: Visual placeholder for future search functionality
- **Privacy**: No message logs stored on servers

## Development

### File Structure
```
secure-wireguard-chat/
├── wg_crypto.py          # Cryptographic primitives
├── client_logic.py       # Protocol implementation
├── main.py              # Application entry point
├── gui/                 # User interface components
└── chat_exports/        # Exported chat histories
```

### Contributing
1. Fork the repository
2. Create a feature branch
3. Implement changes with tests
4. Submit a pull request

## License

[Specify your license here]

## Acknowledgments

- WireGuard protocol specification
- University of Cape Town Computer Science Department
- Cryptographic implementations based on industry standards

## Screenshots

The application features a modern dark theme interface with:
- Split-panel design for optimal workflow
- Contextual right-click menus
- Real-time user and channel lists
- Clean message display with timestamps
- Responsive sidebar navigation

For detailed GUI screenshots, refer to the included user guide documentation.

---

*Built with security and user experience in mind. All communications are protected by state-of-the-art encryption.*
