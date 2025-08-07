# wg_crypto.py
import nacl.bindings
import nacl.public
import hashlib
import hmac
import struct
import time
import base64
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.exceptions import InvalidTag as CryptoInvalidTag
import os # For path joining
import sys # For exiting on critical error

CONSTRUCTION = b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"
IDENTIFIER   = b"WireGuard v1 zx2c4 Jason@zx2c4.com"
LABEL_MAC1   = b"mac1----"

# --- Read private key from privateKey.txt ---
PRIVATE_KEY_FILE = "privateKey.txt"
YOUR_BASE64_SECRET_KEY_FROM_FILE = None

try:
    # Construct path relative to this script file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_file_path = os.path.join(script_dir, PRIVATE_KEY_FILE)

    if not os.path.exists(key_file_path):
        raise FileNotFoundError(f"{PRIVATE_KEY_FILE} not found at {key_file_path}. "
                                f"Please create it and put your Base64 private key inside.")

    with open(key_file_path, "r") as f:
        YOUR_BASE64_SECRET_KEY_FROM_FILE = f.read().strip()
    
    if not YOUR_BASE64_SECRET_KEY_FROM_FILE:
        raise ValueError(f"{PRIVATE_KEY_FILE} is empty. It should contain your Base64 private key.")

except Exception as e:
    print(f"CRITICAL ERROR reading private key from {PRIVATE_KEY_FILE}: {e}")
    print("Please ensure the file exists in the same directory as wg_crypto.py,")
    print("is readable, and contains your Base64 encoded private key on a single line.")
    # We can't proceed without a key, so exit or use a placeholder that will cause errors
    YOUR_BASE64_SECRET_KEY_FROM_FILE = "ERROR_READING_KEY_FILE" # This will cause subsequent errors

# --- Decode and Validate Keys ---
try:
    if YOUR_BASE64_SECRET_KEY_FROM_FILE == "ERROR_READING_KEY_FILE":
        # This means the file read failed, and we should not attempt to decode
        YOUR_STATIC_PRIVATE_KEY_BYTES = b"ERROR_KEY_FILE_READ_FAILED!"
    else:
        YOUR_STATIC_PRIVATE_KEY_BYTES = base64.b64decode(YOUR_BASE64_SECRET_KEY_FROM_FILE)
    
    if len(YOUR_STATIC_PRIVATE_KEY_BYTES) != 32:
        # This check will also catch the placeholder if file read failed
        raise ValueError(f"Decoded private key is not 32 bytes long! Length: {len(YOUR_STATIC_PRIVATE_KEY_BYTES)}. "
                         f"Ensure {PRIVATE_KEY_FILE} contains a valid Base64 encoded Curve25519 private key.")
except Exception as e:
    print(f"CRITICAL ERROR processing your private key: {e}")
    YOUR_STATIC_PRIVATE_KEY_BYTES = b"ERROR_INVALID_PRIVATE_KEY_BYTES!"

try:
    if YOUR_STATIC_PRIVATE_KEY_BYTES not in [b"ERROR_KEY_FILE_READ_FAILED!", b"ERROR_INVALID_PRIVATE_KEY_BYTES!"]:
        your_priv_for_pub = nacl.public.PrivateKey(YOUR_STATIC_PRIVATE_KEY_BYTES)
        YOUR_STATIC_PUBLIC_KEY_BYTES = bytes(your_priv_for_pub.public_key)
        if len(YOUR_STATIC_PUBLIC_KEY_BYTES) != 32:
            raise ValueError("Derived public key is not 32 bytes")
    else:
        YOUR_STATIC_PUBLIC_KEY_BYTES = b"ERROR_CANNOT_DERIVE_PUBLIC_KEY!"
except Exception as e:
    print(f"Error deriving your public key from private key: {e}")
    YOUR_STATIC_PUBLIC_KEY_BYTES = b"ERROR_DERIVING_YOUR_PUBLIC_KEY!"

SERVER_BASE64_PUBLIC_KEY = "ZixewENi85M3vxEUIu0TC5/nrzuUsHAT4ZTdhc8BC0M="
try:
    SERVER_STATIC_PUBLIC_KEY_BYTES = base64.b64decode(SERVER_BASE64_PUBLIC_KEY)
    if len(SERVER_STATIC_PUBLIC_KEY_BYTES) != 32:
        raise ValueError("Decoded SERVER_STATIC_PUBLIC_KEY_BYTES is not 32 bytes long!")
except Exception as e:
    print(f"CRITICAL ERROR with SERVER_BASE64_PUBLIC_KEY: {e}")
    SERVER_STATIC_PUBLIC_KEY_BYTES = b"ERROR_INVALID_SERVER_PUBLIC_KEY!"


# --- Cryptographic Primitives (Unchanged from previous version) ---

def DH_GENERATE() -> tuple[bytes, bytes]:
    private_key_obj = nacl.public.PrivateKey.generate()
    return (bytes(private_key_obj), bytes(private_key_obj.public_key))

def DH(private_key: bytes, public_key: bytes) -> bytes:
    if len(private_key) != 32 or len(public_key) != 32:
        # Check for placeholder error bytes before raising ValueError for length
        if private_key.startswith(b"ERROR_") or public_key.startswith(b"ERROR_"):
             raise ValueError(f"DH called with invalid key material due to earlier errors. Private: {private_key[:20]}, Public: {public_key[:20]}")
        raise ValueError("DH keys must be 32 bytes.")
    return nacl.bindings.crypto_scalarmult(private_key, public_key)

def AEAD(key: bytes, counter: int, plaintext: bytes, authtext: bytes) -> bytes:
    if len(key) != 32: raise ValueError("AEAD key must be 32 bytes.")
    nonce = b'\x00\x00\x00\x00' + struct.pack("<Q", counter)
    chacha = ChaCha20Poly1305(key)
    return chacha.encrypt(nonce, plaintext, authtext)

def AEAD_decrypt(key: bytes, counter: int, ciphertext_with_tag: bytes, authtext: bytes) -> bytes:
    if len(key) != 32: raise ValueError("AEAD key must be 32 bytes.")
    nonce = b'\x00\x00\x00\x00' + struct.pack("<Q", counter)
    chacha = ChaCha20Poly1305(key)
    try:
        return chacha.decrypt(nonce, ciphertext_with_tag, authtext)
    except CryptoInvalidTag:
        raise ValueError("AEAD decryption failed (invalid tag or ciphertext)")

def HASH(input_data: bytes) -> bytes:
    return hashlib.blake2s(input_data, digest_size=32).digest()

def HMAC_hash(key: bytes, input_data: bytes) -> bytes:
    return hmac.new(key, input_data, hashlib.blake2s).digest()

def MAC(key: bytes, input_data: bytes) -> bytes:
    return hashlib.blake2s(input_data, key=key, digest_size=16).digest()

def TAI64N() -> bytes:
    current_unix_ns = time.time_ns()
    tai_seconds = (current_unix_ns // 10**9) + (2**62) + 10
    nanoseconds = current_unix_ns % 10**9
    return struct.pack(">Q", tai_seconds) + struct.pack(">I", nanoseconds)

def KDF_Chain_Key_AEADKey(chaining_key_input: bytes, input_material: bytes) -> tuple[bytes, bytes]:
    temp = HMAC_hash(chaining_key_input, input_material)
    new_chaining_key = HMAC_hash(temp, b'\x01')
    aead_key = HMAC_hash(temp, new_chaining_key + b'\x02')
    return new_chaining_key, aead_key

def KDF_Chain_Only(chaining_key_input: bytes, input_material: bytes) -> bytes:
    temp = HMAC_hash(chaining_key_input, input_material)
    new_chaining_key = HMAC_hash(temp, b'\x01')
    return new_chaining_key

def KDF_Responder_Handshake(chaining_key_input: bytes, preshared_key: bytes) -> tuple[bytes, bytes, bytes]:
    temp = HMAC_hash(chaining_key_input, preshared_key)
    new_chaining_key = HMAC_hash(temp, b'\x01')
    temp2_for_hash = HMAC_hash(temp, new_chaining_key + b'\x02')
    key_for_aead = HMAC_hash(temp, temp2_for_hash + b'\x03')
    return new_chaining_key, temp2_for_hash, key_for_aead

def KDF_Transport_Keys(chaining_key_input: bytes) -> tuple[bytes, bytes]:
    temp1 = HMAC_hash(chaining_key_input, b'')
    key_one = HMAC_hash(temp1, b'\x01')
    key_two = HMAC_hash(temp1, key_one + b'\x02')
    return key_one, key_two

# --- ALIASES AND DEFINITIONS for client_logic.py ---
DH_Generate = DH_GENERATE # Alias for consistency if client_logic uses this exact name
Hash = HASH
Mac = MAC
AEAD_encrypt = AEAD # Alias for consistency
Timestamp = TAI64N

def MixHash(h: bytes, data: bytes) -> bytes:
    return HASH(h + data)

Kdf1 = KDF_Chain_Only

def Kdf2(chaining_key: bytes, input_material: bytes) -> tuple[bytes, bytes]:
    if input_material == b'': # For deriving transport keys
        return KDF_Transport_Keys(chaining_key)
    else: # For handshake steps
        return KDF_Chain_Key_AEADKey(chaining_key, input_material)

Kdf3 = KDF_Responder_Handshake

# --- Final check for critical key errors before module is fully imported ---
# This helps catch issues early if the GUI tries to run with bad keys.
if YOUR_STATIC_PRIVATE_KEY_BYTES.startswith(b"ERROR_") or \
   YOUR_STATIC_PUBLIC_KEY_BYTES.startswith(b"ERROR_") or \
   SERVER_STATIC_PUBLIC_KEY_BYTES.startswith(b"ERROR_"):
    print("\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    print("!!! CRITICAL KEY INITIALIZATION FAILURE IN wg_crypto.py.                   !!!")
    print("!!! The application cannot proceed securely. Please check console errors.  !!!")
    print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n")
    # Depending on how critical this is, you might want to sys.exit(1) here
    # to prevent the application from attempting to run with invalid keys.
    # For now, it will print the error and allow the import, but operations will fail.