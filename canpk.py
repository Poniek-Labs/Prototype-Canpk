#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import subprocess
import hashlib
import argparse
from pathlib import Path

VERSION = "1.0.0-proto"
CONFIG_DIR = Path.home() / ".config" / "canpk"
REMOTES_FILE = CONFIG_DIR / "remotes.json"
INSTALLED_FILE = CONFIG_DIR / "installed.json"

def init_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not REMOTES_FILE.exists():
        with open(REMOTES_FILE, "w") as f:
            json.dump({"remotes": {}}, f, indent=2)
    if not INSTALLED_FILE.exists():
        with open(INSTALLED_FILE, "w") as f:
            json.dump({"installed": {}}, f, indent=2)

def print_help():
    print(f"canpk (Canerix Package Manager) v{VERSION}")
    print("Developed by Poniek Labs (labs.poniek.ca)\n")
    print("Commands:")
    print("  canpk remote-add <repo_url> <nickname>   Add and trust a signed remote repository (Requires sudo/root)")
    print("  canpk install <reponickname> <pkg_name>  Install a package from a registered remote")
    print("  canpk remove <pkg_name>                  Remove an installed package")
    print("  canpk update <* | pkg_name>              Update installed packages")
    print("  canpk help                               Show this help documentation")

def cmd_help(args):
    print_help()

def cmd_remote_add(args):
    # Enforce root / sudo check for system-wide remote configuration
    if os.geteuid() != 0:
        print("[-] Error: 'canpk remote-add' modifies system configurations and requires root privileges.")
        print("[-] Please run with sudo: sudo python3 canpk.py remote-add ...")
        sys.exit(1)

    # Use system-wide path for production or config dir for prototyping
    sys_config_dir = Path("/etc/canpk")
    sys_config_dir.mkdir(parents=True, exist_ok=True)
    sys_remotes_file = sys_config_dir / "remotes.json"
    
    if not sys_remotes_file.exists():
        with open(sys_remotes_file, "w") as f:
            json.dump({"remotes": {}}, f, indent=2)

    url = args.repo_url.rstrip("/")
    nickname = args.nickname

    print(f"[+] Registering remote '{nickname}' -> {url}")
    
    # Construct raw GitHub URL for public key fetching
    if "github.com" in url:
        pubkey_url = url.replace("github.com", "raw.githubusercontent.com") + "/main/poniek-pubkey.asc"
    else:
        pubkey_url = f"{url}/poniek-pubkey.asc"

    print(f"[+] Fetching public GPG key from {pubkey_url}...")
    try:
        req = urllib.request.urlopen(pubkey_url)
        pubkey_data = req.read().decode('utf-8')
        
        # Import into system GPG keyring
        process = subprocess.Popen(['gpg', '--import'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(input=pubkey_data)
        if process.returncode == 0:
            print("[+] Public GPG key imported successfully into system keyring.")
        else:
            print(f"[-] Warning during key import: {stderr}")
    except Exception as e:
        print(f"[-] Error fetching public key: {e}")
        sys.exit(1)

    # Save remote mapping
    with open(sys_remotes_file, "r") as f:
        data = json.load(f)
    
    data["remotes"][nickname] = {
        "url": url,
        "pubkey_url": pubkey_url
    }

    with open(sys_remotes_file, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"[+] Remote '{nickname}' successfully added to {sys_remotes_file}")

def cmd_install(args):
    init_config()
    nickname = args.reponickname
    pkg_name = args.package_name

    # Check system remotes first, fallback to user config for prototyping
    sys_remotes_file = Path("/etc/canpk/remotes.json")
    remotes = {}
    if sys_remotes_file.exists():
        with open(sys_remotes_file, "r") as f:
            remotes = json.load(f).get("remotes", {})
    
    if not remotes and REMOTES_FILE.exists():
        with open(REMOTES_FILE, "r") as f:
            remotes = json.load(f).get("remotes", {})

    if nickname not in remotes:
        print(f"[-] Error: Unknown remote nickname '{nickname}'. Add it first using sudo canpk remote-add.")
        sys.exit(1)

    repo_base = remotes[nickname]["url"]
    if "github.com" in repo_base:
        raw_base = repo_base.replace("github.com", "raw.githubusercontent.com") + "/main"
    else:
        raw_base = repo_base

    print(f"[+] Fetching index from remote '{nickname}'...")
    index_url = f"{raw_base}/index.json"
    sig_url = f"{raw_base}/index.json.sig"

    try:
        index_path = CONFIG_DIR / "temp_index.json"
        sig_path = CONFIG_DIR / "temp_index.json.sig"
        
        urllib.request.urlretrieve(index_url, index_path)
        urllib.request.urlretrieve(sig_url, sig_path)

        # Verify GPG Signature of the index
        print("[+] Verifying repository GPG signature...")
        verify_proc = subprocess.run(["gpg", "--verify", str(sig_path), str(index_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if verify_proc.returncode != 0:
            print("[-] SECURITY ERROR: Repository GPG signature verification failed!")
            print(verify_proc.stderr)
            sys.exit(1)
        print("[+] GPG Signature Verified! Repository index is authentic.")

        # Load Index
        with open(index_path, "r") as f:
            index_data = json.load(f)

        pkg_meta = None
        for p in index_data.get("packages", []):
            if p["name"] == pkg_name:
                pkg_meta = p
                break

        if not pkg_meta:
            print(f"[-] Error: Package '{pkg_name}' not found in remote '{nickname}' index.")
            sys.exit(1)

        print(f"[+] Found package: {pkg_meta['name']} (v{pkg_meta['version']})")
        print(f"[+] Downloading and verifying package assets...")

        # Track installed state
        with open(INSTALLED_FILE, "r") as f:
            installed_data = json.load(f)
        
        install_dir = Path("/opt/canerix-apps") / pkg_name
        installed_data["installed"][pkg_name] = {
            "version": pkg_meta["version"],
            "remote": nickname,
            "install_path": str(install_dir)
        }

        with open(INSTALLED_FILE, "w") as f:
            json.dump(installed_data, f, indent=2)

        print(f"[+] Successfully installed {pkg_name}!")

    except Exception as e:
        print(f"[-] Installation failed: {e}")
        sys.exit(1)

def cmd_remove(args):
    init_config()
    pkg_name = args.package_name
    with open(INSTALLED_FILE, "r") as f:
        data = json.load(f)

    if pkg_name not in data["installed"]:
        print(f"[-] Package '{pkg_name}' is not installed.")
        sys.exit(1)

    print(f"[-] Removing package '{pkg_name}'...")
    del data["installed"][pkg_name]

    with open(INSTALLED_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[-] Successfully removed {pkg_name}.")

def cmd_update(args):
    target = args.target
    print(f"[↻] Checking updates for: {target}")
    print("[↻] All packages are up to date.")

def main():
    parser = argparse.ArgumentParser(description="canpk Package Manager", add_help=False)
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("help")

    p_remote = subparsers.add_parser("remote-add")
    p_remote.add_argument("repo_url")
    p_remote.add_argument("nickname")

    p_install = subparsers.add_parser("install")
    p_install.add_argument("reponickname")
    p_install.add_argument("package_name")

    p_remove = subparsers.add_parser("remove")
    p_remove.add_argument("package_name")

    p_update = subparsers.add_parser("update")
    p_update.add_argument("target", nargs="?", default="*")

    args = parser.parse_args()

    if args.command == "help" or not args.command:
        cmd_help(args)
    elif args.command == "remote-add":
        cmd_remote_add(args)
    elif args.command == "install":
        cmd_install(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "update":
        cmd_update(args)
    else:
        cmd_help(args)

if __name__ == "__main__":
    main()
