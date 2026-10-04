"""
Encrypts a real bank CSV export at rest, for anyone who wants to run this
pipeline against their own actual transactions instead of (or alongside)
the synthetic sample data.

Why this exists: data/raw/*.csv is already gitignored except the sample
file (see .gitignore), so a real export dropped there was never at risk
of being committed. But "gitignored" only protects you from git -- the
file still sits in plaintext on disk, in the repo folder, for as long as
it's there. This script closes that gap: your real export gets encrypted
immediately with a passphrase only you know, the plaintext copy is then
deleted, and ingestion/load_to_duckdb.py decrypts it straight into memory
at load time -- a plaintext copy of your real data is never written back
to disk by this pipeline.

What this does *not* protect against: someone with control of the machine
while the pipeline is actually running (it's plaintext in memory for that
window, same as any program that processes your data), or a weak/guessed
passphrase. It protects the at-rest file sitting on disk the rest of the
time, and it means your real export is never silently swept up by a
backup tool, git add -A, zipping up the repo to send someone, etc.

Usage:
    python3 scripts/encrypt_bank_csv.py path/to/your_real_export.csv

    Prompts for a passphrase (twice, to confirm), writes
    data/private/your_real_export.csv.enc, verifies it decrypts back to
    the original bytes, and only then deletes the plaintext input file.

To load the encrypted file into the pipeline:
    export BANK_DATA_PASSPHRASE="..."     # or you'll be prompted
    python3 ingestion/load_to_duckdb.py

Rename your bank's export columns to match REQUIRED_COLUMNS in
ingestion/load_to_duckdb.py *before* encrypting it -- see the README
section "Using your own real transactions."
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ingestion"))
import crypto_utils  # noqa: E402

PRIVATE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "private")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/encrypt_bank_csv.py path/to/your_real_export.csv")
        sys.exit(1)

    src_path = sys.argv[1]
    if not os.path.isfile(src_path):
        print(f"No such file: {src_path}")
        sys.exit(1)
    if not src_path.lower().endswith(".csv"):
        print("Expected a .csv file.")
        sys.exit(1)

    with open(src_path, "rb") as f:
        plaintext = f.read()

    print(f"Encrypting {src_path} ({len(plaintext):,} bytes)...")
    passphrase = crypto_utils.prompt_passphrase(confirm=True)

    blob = crypto_utils.encrypt_bytes(plaintext, passphrase)

    # Verify round-trip *before* touching the original file -- if this
    # fails, your plaintext file is untouched and nothing was deleted.
    roundtrip = crypto_utils.decrypt_bytes(blob, passphrase)
    if roundtrip != plaintext:
        print("Encryption self-check failed -- not deleting your original file. "
              "Please try again or report this.")
        sys.exit(1)

    os.makedirs(PRIVATE_DIR, exist_ok=True)
    out_name = os.path.basename(src_path) + ".enc"
    out_path = os.path.join(PRIVATE_DIR, out_name)
    with open(out_path, "wb") as f:
        f.write(blob)

    os.remove(src_path)

    print(f"Wrote {out_path} ({len(blob):,} bytes, encrypted).")
    print(f"Deleted the plaintext original at {src_path}.")
    print()
    print("To load this into the pipeline:")
    print('  export BANK_DATA_PASSPHRASE="..."   # the passphrase you just entered')
    print("  python3 ingestion/load_to_duckdb.py")
    print()
    print("data/private/ is gitignored -- this file will never be committed.")


if __name__ == "__main__":
    main()
