import math
import random

from . import hmac
from .utils import get_msb, xor_b
from .exceptions import CryptographyException, DecryptionFailureException

s_box = [[0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76],
         [0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0],
         [0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15],
         [0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75],
         [0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84],
         [0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf],
         [0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8],
         [0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2],
         [0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73],
         [0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb],
         [0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79],
         [0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08],
         [0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a],
         [0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e],
         [0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf],
         [0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16]]


def invert_sbox(s_box: list) -> list:
    """Invert a given substitution box such that looking 
    up an element gives the index of that element in 
    the original s box.

    Args:
        s_box (list): 2D list containing the original SBox

    Returns:
        list: 2D list containing the inverted SBox
    """

    inverted_s_box = [[0] * 16 for _ in range(16)]
    for row_i, row in enumerate(s_box):
        for col_i, byte in enumerate(row):
            upper = byte >> 4
            lower = byte % 2**4 
            inverted_s_box[upper][lower] = (row_i << 4) | col_i
    return inverted_s_box
inverse_s_box = invert_sbox(s_box)


def add_pkcs7(data: bytes) -> bytes:
    """Add PKCS7 padding to the data to make it a multiple
    of 16 bytes in length.
    """
    to_pad = 16 - len(data) % 16
    data += (to_pad) * to_pad.to_bytes(1, 'big')
    return data

def remove_pkcs7(data: bytes) -> bytes:
    """Remove PKCS#7 padding from some data to retrieve the
    original data prior to being padded."""
    num_padded = data[-1]
    if num_padded > 16 or num_padded == 0:
        raise ValueError("Invalid Padding")
    if len(data) < num_padded:
        raise ValueError("Invalid Padding")
    for i in range(1, num_padded + 1):
        if data[-i] != num_padded:
            raise ValueError("Invalid Padding")
    return data[:len(data) - num_padded]

def split_blocks(data: bytes) -> list:
    """Split data into 16 byte chunks. Data MUST be 
    a multiple of 16 bytes in length.

    Args:
        data (int): The integer to split

    Returns:
        list: A list of 16 byte blocks
    """
    if len(data) % 16 != 0:
        raise ValueError("Data length is not a multiple of 16")
    blocks = [data[i:i+16] for i in range(0, len(data), 16)]
    return blocks


def split_bytes(n: int, byte_count: int = 4) -> list:
    """Convert an integer of any length (default 4 bytes) into an array of bytes.

    Args:
        i (int): The word to split
        byte_count (int, optional): The amount of bytes required to store the word. Defaults to 4.
        Set to 0 to use the lowest amount of bytes possible to hold the word.

    Raises:
        Exception: If the amount of bytes specified is too small to hold the word.

    Returns:
        list: An array of 1 byte integers in big endian order
    """
    if n == 0:
        bit_length = 0
    else:
        bit_length = get_msb(n) + 1

    byte_length = math.ceil(bit_length / 8)
    if byte_length > byte_count:
        raise Exception(f"Need at least {byte_length} bytes to hold {n}.")
    byte_array = []
    for i in range(0, byte_length):
        byte = n % (2**((byte_length-i)*8)) >> (byte_length - i - 1) * 8
        byte_array.append(byte)
    while len(byte_array) < byte_count:
        byte_array.insert(0, 0x00000000)
    return byte_array


def combine_byte_array(byte_array: list) -> int:
    """Combine an array of bytes (big endian) to a single integer.

    Args:
        byte_array (list): An array of byte-long integers in big endian order

    Returns:
        int: The bytes combined into one integer
    """
    word = 0
    for i, b in enumerate(byte_array):
        word |= b << 8 * (len(byte_array) - 1 - i)
    return word


def byte_to_bits(x: int) -> list:
    """Split a single byte integer into an array of individual bits

    Args:
        x (int): A single byte long integer.

    Returns:
        list: List of 1s and 0s
    """
    cur = x
    bits = [0] * 8
    if x >= 256:
        raise Exception(f"Integer {x} is not 1 byte.")
    for i in range(7, -1, -1):
        if cur >= 2 ** i:
            bits[7 - i] = 1
            cur -= 2 ** i
    return bits


# Galois field arithmetic operations
def gf_mod_bytes(b: int, mod: int) -> int:
    """Calculate the modulus of a division of two bytes representing polynomials
    in an order 2^8 galois field (eg 138 = 10001010 = x^7 + x^3 + x^1).


    Args:
        b (int): The dividend
        mod (int): The divisor

    Returns:
        int: The remainder (modulus)
    """
    b_msb = get_msb(b)
    mod_msb = get_msb(mod)
    while b_msb >= mod_msb:
        shifted_mod = mod << (b_msb - mod_msb)
        b ^= shifted_mod
        b_msb = get_msb(b)
        mod_msb = get_msb(mod)
    return b


def gf_multiply_bytes(x: int, y: int, modulus: int = 0x11b) -> int:
    """Calculate the product of two galois field polynomials represented as bytes
    (eg 171 = 0b10101011 = x^7 + x^5 + x^3 + x^1 + 1). This is defined as the
    standard polynomial multiplication of the two polynomials modulus another
    polynomial in the field.

    Args:
        x (int): The multiplicand
        y (int): The multiplier
        modulus (int, optional): Defaults to 0x11b = 0b100011011.

    Returns:
        int: The modular product

    """
    y_coefficients = byte_to_bits(y)
    z = 0
    for i in range(8):
        z ^= (x << i) * y_coefficients[7 - i]
    return gf_mod_bytes(z, modulus)


multiply_lookup = []
for i in range(256):
    row = []
    for j in range(256):
        row.append(gf_multiply_bytes(i, j))
    multiply_lookup.append(row)


def transpose_matrix(m: list) -> list:
    """Transpose a column/row major 4x4 matrix to row/column major.

    Args:
        m (list): 2D list containing the matrix to be transposed

    Returns:
        list: 2D list containing the transposed matrix
    """

    return [[m[0][0], m[1][0], m[2][0], m[3][0]],
            [m[0][1], m[1][1], m[2][1], m[3][1]],
            [m[0][2], m[1][2], m[2][2], m[3][2]],
            [m[0][3], m[1][3], m[2][3], m[3][3]]]


def sbox_lookup(b: int, sbox: list) -> int:
    """Lookup a byte in a given substitution box and
    return the resultant element.

    Args:
        b (int): The byte to lookup
        sbox (list): The sbox to use

    Returns:
        int: The corresponding sbox element
    """
    upper_nibble = b >> 4
    lower_nibble = b % 2 ** 4
    return sbox[upper_nibble][lower_nibble]


def sub_bytes(state: list, inverse: bool = False) -> list:
    """Substitute all bytes in the state matrix
    with their corresponding elements in the
    AES s-box or inverse s-box.

    Args:
        state (list): The current AES block state matrix.
        inverse (bool, optional): Whether to use the inverse s-box. 
            Defaults to False.

    Returns:
        list: The new state matrix
    """

    subbed_state = []
    for row in state:
        subbed = []
        for byte in row:
            if inverse:
                subbed.append(sbox_lookup(byte, inverse_s_box))
            else:
                subbed.append(sbox_lookup(byte, s_box))
        subbed_state.append(subbed)
    return subbed_state


def shift_rows(state: list, inverse: bool = False) -> list:
    """Cyclically shift (rotate) each row in the state left (right if inverse)
    by an increment of 1 each row.
    [a1 a2 a3 a4] -> [a1 a2 a3 a4]
    [b1 b2 b3 b4]    [b2 b3 b4 b1]
    [c1 c2 c3 c4]    [c3 c4 c1 c2]
    [d1 d2 d3 d4]    [d4 d1 d2 d3]

    Args:
        state (list): The current AES state matrix
        inverse (bool, optional): Whether perform the inverse row shift. 
            (Default False).

    Returns:
        list: The shifted state matrix
    """
    rotated = []
    for r, row in enumerate(state):
        shifted_row = []
        for c in range(len(row)):
            shifted_row.append(row[c-r if inverse else (c+r) % 4])
        rotated.append(shifted_row)
    return rotated


def mix_columns(state: list, inverse: bool = False) -> list:
    """Multiply the state matrix with another 4x4 matrix 
    formed from a galois field polynomial incrementally 
    cyclically shifted right.

    Args:
        state (list): The current 4x4 state matrix
        inverse (bool, optional): Whether to invert the operation. (Default False)

    Returns:
        list: The resultant state matrix.
    """
    if inverse:
        multiplication_matrix = shift_rows([[0x0e, 0x0b, 0x0d, 0x09]]*4, True)
    else:
        multiplication_matrix = shift_rows([[0x02, 0x03, 0x01, 0x01]]*4, True)

    mixed_columns = []
    for c in range(4):
        col = [0] * 4
        for i in range(4):
            val = 0
            for j in range(4):
                val ^= multiply_lookup[state[j][c]][multiplication_matrix[i][j]]
            col[i] = val
        mixed_columns.append(col)
    return transpose_matrix(mixed_columns)


def add_round_key(state: list, round_key: list) -> list:
    """Combine the state matrix with a round key matrix by
    XORing together the corresponding elements.

    Args:
        state (list): The current 4x4 state matrix.
        round_key (list): The 4x4 round key matrix
            for the current cipher round.

    Returns:
        list: The state ^ round key
    """
    new_state = []
    for i in range(4):
        row = []
        for j in range(4):
            row.append(state[i][j] ^ round_key[i][j])
        new_state.append(row)
    return new_state


def int_to_word_array(x: int, words: int = 4) -> list:
    """Convert an integer of any length into an array of 4 byte (32 bit) words.

    Args:
        x (int): The integer to split.
        words (int, optional): The amount of words to hold the integer in.
        Defaults to 0 (minimum amount of words required).

    Raises:
        Exception: If the specified amount of words is less than the minimum required to hold the integer.

    Returns:
        list: The words in big endian order.
    """
    if x == 0:
        bit_length = 0
    else:
        bit_length = get_msb(x) + 1

    bit_length_as_multiple_of_32 = 32 * math.ceil(bit_length / 32)
    word_array = []
    if bit_length_as_multiple_of_32 / 32 > words:
        raise Exception(f"NEED AT LEAST {bit_length_as_multiple_of_32 / 32} words to represent the integer {x}")
    for i in range(bit_length_as_multiple_of_32, 0, -32):
        word_array.append((x % 2 ** i) >> i - 32)
    while len(word_array) < words:
        word_array.insert(0, 0x00000000)

    return word_array


def word_array_to_4x4_matrix(words: list) -> list:
    """Convert an array of <=4 words in big endian order to a column major 4x4 byte matrix.

    Args:
        words (list): <=4 32 bit words in big endian order

    Raises:
        Exception: If the amount of words cannot be held in 16 bytes.

    Returns:
        list: A 4x4 byte matrix.
    """
    if len(words) > 4:
        raise Exception(f"Cannot create a 4x4 byte matrix out of {len(words)} words")
    while len(words) < 4:
        words.insert(0, 0x00000000)
    mat = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    for i in range(4):
        word_bytes = split_bytes(words[i])
        for j in range(4):
            mat[j][i] = word_bytes[j]
    return mat


def bytes_to_matrix(x: bytes) -> list:
    """
    Convert a 16 byte block into a 4x4 column major matrix
    """
    if len(x) != 16:
        raise ValueError("Length of block is not 16 bytes")
    matrix = [
        [x[0], x[4], x[8], x[12]], 
        [x[1], x[5], x[9], x[13]], 
        [x[2], x[6], x[10], x[14]], 
        [x[3], x[7], x[11], x[15]]
    ]
    return matrix

def matrix_to_bytes(m: list) -> bytes:
    """Concatenate a 4x4 byte matrix into a 16 byte long string
    
    Args:
        m (list): The byte matrix
    
    Returns:
        bytes: The resultant bytestring"""
    string = b''
    for byte in sum(zip(*m), start=()):
        string += byte.to_bytes(1, 'big')
    return string

def matrix_to_int(m: list) -> int:
    """Concatenate a 4x4 column major byte matrix into a 16 byte (128 bit) integer.

    Args:
        m (list): The byte matrix

    Returns:
        int: The resulting concatenated integer
    """
    shift = 128 - 8
    result = 0
    transposed = transpose_matrix(m)
    for r in transposed:
        for c in r:
            result |= (c << shift)
            shift -= 8
    return result


def round_constant(n: int):
    """Calculates the nth round constant for the AES key expansion.

    Args:
        n (int): The round constant to generate, should be the key schedule row divided by 8 (only for exact multiples)

    Returns:
        int: The calculated 32 bit round constant
    """
    return (2**(n-1)) << 24


def sub_word(word: int):
    """Substitute all bytes in a given word with their corresponding elements in the AES S Box

    Args:
        word (int): The word to subsitute bytes for

    Returns:
        int: The resultant word
    """
    return combine_byte_array(sub_bytes([split_bytes(word)])[0])


def rotate_word(word: int) -> int:
    """Perform a one byte left rotation on a 4 byte word

    Args:
        word (int): An integer word of length 4 bytes

    Returns:
        int: The result of a left byte rotation on the word
    """
    word_bytes = split_bytes(word)
    return combine_byte_array([word_bytes[1], word_bytes[2], word_bytes[3], word_bytes[0]])


def expand_key(key: int) -> list:
    """Perform an aes key expansion on a given 256 bit key to produce
    60 words to be used as round keys in the AES cipher

    Args:
        key (int): A 256 bit integer to be used as the encryption key

    Returns:
        list: The 60 word key schedule
    """
    key_words = int_to_word_array(key, 8)
    schedule = []
    for word in key_words:
        schedule.append(word)
    for i in range(8, 60):
        prev = schedule[i - 1]
        if i % 8 == 0:
            prev = rotate_word(prev)
            prev = sub_word(prev)
            rcon = round_constant(int(i/8))
            prev ^= rcon
        elif (i - 4) % 8 == 0:
            prev = sub_word(prev)
        schedule.append(prev ^ schedule[i - 8])
    return schedule


def cipher_round(state: list, round_key: list) -> list:
    """Perform one round of the AES cipher algorithm

    Args:
        state (list): The current AES state matrix
        round_key (list): 4 words making up the round key for this round

    Returns:
        list: The state matrix after the cipher round has been applied
    """
    round_key_matrix = word_array_to_4x4_matrix(round_key)
    state = sub_bytes(state)
    state = shift_rows(state)
    state = mix_columns(state)
    state = add_round_key(state, round_key_matrix)
    return state


def decipher_round(state: list, round_key: list) -> list:
    """Perform one round of the AES decipher algorithm

    Args:
        state (list): The current AES state matrix
        round_key (list): 4 words making up the round key for this round

    Returns:
        list: The state matrix after the decipher round has been applied
    """
    round_key_matrix = word_array_to_4x4_matrix(round_key)
    state = shift_rows(state, True)
    state = sub_bytes(state, True)
    state = add_round_key(state, round_key_matrix)
    state = mix_columns(state, True)
    return state


def encrypt_block(key_schedule: list, block: bytes) -> bytes:
    """Encrypt a 128 bit message block using 14 AES rounds

    Args:
        key_schedule (list): The key schedule derived from a 256 bit encryption key
        block (int): The 128 bit message block to encrypt

    Returns:
        int: The resultant 128 bit ciphertext block
    """
    state = bytes_to_matrix(block)
    r_0_round_key = word_array_to_4x4_matrix(key_schedule[0:4])
    state = add_round_key(state, r_0_round_key)
    for r in range(1, 14):
        state = cipher_round(state, key_schedule[r*4:(r + 1)*4])
    state = sub_bytes(state)
    state = shift_rows(state)
    state = add_round_key(state, word_array_to_4x4_matrix(key_schedule[56:60]))
    return matrix_to_bytes(state)


def decrypt_block(key_schedule: list, block: bytes) -> bytes:
    """Decrypt one 128 bit ciphertext block using 14 AES rounds

    Args:
        key_schedule (list): The key schedule derived from a 256 bit encryption key
        block (int): The 128 bit ciphertext block to decrypt

    Returns:
        int: The resultant 128 bit message block
    """
    state = bytes_to_matrix(block)
    r_0_round_key = word_array_to_4x4_matrix(key_schedule[56:60])
    state = add_round_key(state, r_0_round_key)
    for r in range(1, 14):
        state = decipher_round(state, key_schedule[56 - (r * 4):60 - (r*4)])
    state = shift_rows(state, True)
    state = sub_bytes(state, True)
    state = add_round_key(state, word_array_to_4x4_matrix(key_schedule[0:4]))
    return matrix_to_bytes(state)


def pad(data: bytes, max_pad_bytes: int = 2048) -> bytes:
    """Add a random number of random padding bytes before
    and after the data.

    Args:
        data (bytes): The data to pad
        max_pad_bytes (int): The maximum number of padding bytes to add. 
            (Default 2048)

    Returns:
        bytes: The padded data
    """
    num_pad_bytes_before = random.randrange(0, max_pad_bytes // 2)
    padded_data = num_pad_bytes_before.to_bytes(2, 'big')
    num_pad_bytes_after = random.randrange(0, max_pad_bytes // 2)
    padded_data += num_pad_bytes_after.to_bytes(2, 'big')
    padding_bytes_before = random.randbytes(num_pad_bytes_before)
    padded_data += padding_bytes_before
    padded_data += data
    padding_bytes_after = random.randbytes(num_pad_bytes_after)
    padded_data += padding_bytes_after
    return padded_data

def unpad(data: bytes) -> bytes:
    """Strip padding bytes from the start and end of
    some padded data.

    Args:
        data (bytes): The padded data.

    Returns:
        bytes: The original data with padding removed.
    """
    num_before = int.from_bytes(data[:2], 'big')
    num_after = int.from_bytes(data[2:4], 'big')
    if num_before + num_after > len(data):
        raise ValueError("Padding is apparently longer than entire data")
    return data[num_before + 4:len(data)-num_after]

def encrypt_cbc(data: bytes, key: int, iv: int = 0, test_mode: bool = False) -> bytes:
    """Encrypt a bytestring using AES in Cipher Block Chaining mode.
    This means that the output of the encryption of each plaintext block
    is XORed with the next plaintext block before it is encrypted which
    creates entropy within the ciphertext and prevents ciphertext analysis.
    The initialisation vector is the value which is XORed with the first block to
    prevent comparing the first blocks of two matching messages.

    Args:
        data (bytes): The plaintext bytestring to encrypt
        key (int): The 256 bit encryption key
        iv (int): The 128 bit initialisation vector to XOR with the first block. 
            It is highly recommended this is provided. (Default 0)
        test_mode (bool): Do not add random padding,
            assume plaintext is of length 16n bytes and do not add PKCS#7 padding,
            do not append an HMAC to the ciphertext.
            This should be set for testing purposes ONLY as it is INSECURE.

    Returns:
        bytes: The encrypted ciphertext bytestring
    """
    if not test_mode:
        data = pad(data)
        data = add_pkcs7(data)
    else:
        if len(data) % 16 != 0:
            raise CryptographyException("No PKCS#7 and plaintext is not a multiple of 16 bytes")

    message_blocks = split_blocks(data)  # split message into blocks
    key_schedule = expand_key(key)
    ciphertext_blocks = []
    prev_output = iv.to_bytes(16, 'big')
    for block in message_blocks:
        xored_block = xor_b(block, prev_output)  # xor with previous block output
        ciphertext_block = encrypt_block(key_schedule, xored_block)
        prev_output = ciphertext_block
        ciphertext_blocks.append(ciphertext_block)

    ciphertext = b''.join(ciphertext_blocks)
    if not test_mode:
        ciphertext_hmac = hmac.calculate(ciphertext, iv, key)
        ciphertext += ciphertext_hmac
    return ciphertext


def decrypt_cbc(ciphertext: bytes, key: int, iv: int, test_mode: bool = False) -> bytes:
    """Decrypt a bytestring using AES in Cipher Block Chaining mode.

    Args:
        ciphertext (bytes): The encrypted ciphertext bytestring
        key (int): The 256 bit encryption key
        iv (int): The 128 bit initialisation vector used when the data was encrypted.
        test_mode (bool): Assume no random padding has been added,
            assume no PKCS#7 padding has been added,
            assume no HMAC has been added.
            This should be used when testing the algorithm ONLY.

    Returns:
        bytes: The decrypted plaintext bytestring
    """
    if not test_mode:
        if len(ciphertext) < 32:
            raise DecryptionFailureException(key)
        ciphertext_hmac = ciphertext[-32:]
        ciphertext = ciphertext[:-32]
    try:
        ciphertext_blocks = split_blocks(ciphertext)
    except ValueError:
        raise DecryptionFailureException(key)
    key_schedule = expand_key(key)
    message_blocks = []
    for i, block in enumerate(ciphertext_blocks):
        if i == 0:
            prev_output = iv.to_bytes(16, 'big')
        else:
            prev_output = ciphertext_blocks[i-1]

        xored_block = decrypt_block(key_schedule, block)
        message_blocks.append(xor_b(xored_block, prev_output))

    plaintext = b''.join(message_blocks)
    if not test_mode:
        if not hmac.verify(ciphertext_hmac, ciphertext, iv, key):
            raise DecryptionFailureException(key)
        try:
            plaintext = remove_pkcs7(plaintext)
        except ValueError:
            raise DecryptionFailureException(key)
        try:
            plaintext = unpad(plaintext)
        except:
            raise DecryptionFailureException(key)
    return plaintext

