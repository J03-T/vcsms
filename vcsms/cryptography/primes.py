import math
import random


def sieve_of_eratosthenes(maximum: int) -> list:
    """Perform the sieve of Eratosthenes to calculate
    all primes up to a given integer.

    Args:
        maximum (int): The upper limit

    Returns:
        list: All the primes up to the maximum
    """
    primes = [0] * (maximum - 1)
    # 0th index = 2
    i = 2
    while i < math.sqrt(maximum):
        if primes[i - 2] == 0:
            for j in range(i ** 2, maximum + 1, i):
                primes[j - 2] = 1
        i += 1

    return [x + 2 for x in filter(lambda n: primes[n] == 0, range(len(primes)))]


primes_up_to_1_million = sieve_of_eratosthenes(1000000)  # calculated on module import


def miller_rabin_primality_test(n: int, r: int):
    if n % 2 == 0 or n == 1:
        return False
    d = n - 1
    s = 0
    # divide d as far as possible
    while d % 2 == 0:
        d //= 2
        s += 1

    for _ in range(r):
        a = random.randrange(2, n - 1)
        b = pow(a, d, n)
        if b in {1, n - 1}:   # +- 1 (mod n)
            continue
        for _ in range(s):
            check_next = False
            b = pow(b, 2, n)
            if b == n - 1:  # likely is prime (divides n)
                check_next = True
                break
        if not check_next:
            return False
    return True


def is_prime(x: int):
    for p in primes_up_to_1_million:
        if x % p == 0 and p != x:
            return False
    if miller_rabin_primality_test(x, 100):
        return True
    return False
