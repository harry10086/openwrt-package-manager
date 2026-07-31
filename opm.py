#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import re
import subprocess
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
OPENWRT_ROOT = os.getcwd()

def parse_yaml(content):
    """
    A simple zero-dependency parser for packages.yml
    """
    repositories = {}
    lines = content.splitlines()
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue
            
        if stripped == 'repositories:':
            i += 1
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    i += 1
                    continue
                
                indent = len(line) - len(line.lstrip())
                if indent == 0:
                    break
                    
                repo_match = re.match(r'^([a-zA-Z0-9_-]+):$', stripped)
                if repo_match:
                    repo_name = repo_match.group(1)
                    repositories[repo_name] = {'url': '', 'packages': []}
                    
                    i += 1
                    while i < len(lines):
                        line = lines[i]
                        stripped = line.strip()
                        if not stripped or stripped.startswith('#'):
                            i += 1
                            continue
                        
                        indent_attr = len(line) - len(line.lstrip())
                        if indent_attr <= indent:
                            break
                            
                        if stripped.startswith('url:'):
                            url_val = stripped[4:].strip()
                            url_val = url_val.strip('\'"')
                            repositories[repo_name]['url'] = url_val
                            i += 1
                        elif stripped == 'packages:':
                            i += 1
                            while i < len(lines):
                                line = lines[i]
                                stripped = line.strip()
                                if not stripped or stripped.startswith('#'):
                                    i += 1
                                    continue
                                
                                indent_pkg = len(line) - len(line.lstrip())
                                if indent_pkg <= indent_attr:
                                    break
                                    
                                if stripped.startswith('-'):
                                    pkg_name = stripped[1:].strip()
                                    pkg_name = pkg_name.strip('\'"')
                                    repositories[repo_name]['packages'].append(pkg_name)
                                i += 1
                        else:
                            i += 1
                else:
                    i += 1
            continue
        i += 1
        
    return {'repositories': repositories}

def load_packages_config():
    paths = [
        os.path.join(os.getcwd(), 'packages.yml'),
        os.path.join(SCRIPT_DIR, 'packages.yml')
    ]
    config_path = None
    for p in paths:
        if os.path.exists(p):
            config_path = p
            break
            
    if not config_path:
        print("[ERROR] packages.yml not found. Please create one.")
        sys.exit(1)
        
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"[ERROR] Failed to read packages.yml: {e}")
        sys.exit(1)
        
    try:
        import yaml
        return yaml.safe_load(content)
    except ImportError:
        return parse_yaml(content)

def parse_dep_token(token):
    token = token.strip().lstrip('+').lstrip('@').lstrip('!')
    if not token:
        return None
    if ':' in token:
        token = token.split(':')[-1]
    token = token.lstrip('+').lstrip('@').lstrip('!')
    token = token.split('/')[0]
    token = token.strip('()')
    return token

def extract_packages_from_makefile(content):
    pkg_name_val = ""
    m = re.search(r'PKG_NAME\s*(?::=|=)\s*([^\s#]+)', content)
    if m:
        pkg_name_val = m.group(1).strip()
        pkg_name_val = pkg_name_val.strip('\'"')
    
    packages = []
    for pkg_match in re.finditer(r'define\s+Package/([^\s\n#]+)', content):
        pkg_def = pkg_match.group(1).strip()
        if '/' in pkg_def:
            continue
        if '$(PKG_NAME)' in pkg_def:
            pkg_def = pkg_def.replace('$(PKG_NAME)', pkg_name_val)
        if '${PKG_NAME}' in pkg_def:
            pkg_def = pkg_def.replace('${PKG_NAME}', pkg_name_val)
        packages.append(pkg_def)
        
    if not packages and pkg_name_val:
        packages.append(pkg_name_val)
        
    return packages

def extract_dependencies_from_makefile(content, pkg_name, pkg_name_val=None):
    if not pkg_name_val:
        m = re.search(r'PKG_NAME\s*(?::=|=)\s*([^\s#]+)', content)
        if m:
            pkg_name_val = m.group(1).strip()
            pkg_name_val = pkg_name_val.strip('\'"')
            
    deps = []
    
    # 1. Parse LUCI_DEPENDS if we are looking for the main package's dependencies
    if pkg_name_val and pkg_name == pkg_name_val:
        in_luci_depends = False
        luci_depends_lines = []
        lines = content.splitlines()
        for line in lines:
            line_stripped = line.strip()
            if not in_luci_depends:
                m = re.match(r'^LUCI_DEPENDS\s*(?::=|=)\s*(.*)', line_stripped)
                if m:
                    in_luci_depends = True
                    val = m.group(1)
                    if val.endswith('\\'):
                        luci_depends_lines.append(val[:-1].strip())
                    else:
                        luci_depends_lines.append(val.strip())
                        break
            else:
                val = line_stripped
                if val.endswith('\\'):
                    luci_depends_lines.append(val[:-1].strip())
                else:
                    luci_depends_lines.append(val.strip())
                    break
                    
        if luci_depends_lines:
            luci_depends_str = ' '.join(luci_depends_lines)
            luci_depends_str = luci_depends_str.replace('$(PKG_NAME)', pkg_name_val)
            luci_depends_str = luci_depends_str.replace('${PKG_NAME}', pkg_name_val)
            for token in luci_depends_str.split():
                dep = parse_dep_token(token)
                if dep and dep not in deps:
                    deps.append(dep)
                    
    # 2. Parse block Package/pkg_name for DEPENDS
    patterns = [re.escape(pkg_name)]
    if pkg_name_val and pkg_name == pkg_name_val:
        patterns.append(re.escape('$(PKG_NAME)'))
        patterns.append(re.escape('${PKG_NAME}'))
        
    block_pattern = r'define\s+Package/(?:' + '|'.join(patterns) + r')(?:\s|$|#)(.*?)\s*endef'
    match = re.search(block_pattern, content, re.DOTALL)
    if match:
        block_content = match.group(1)
        depends_lines = []
        lines = block_content.splitlines()
        in_depends = False
        for line in lines:
            line_stripped = line.strip()
            if not in_depends:
                m = re.match(r'^DEPENDS\s*(?::=|=)\s*(.*)', line_stripped)
                if m:
                    in_depends = True
                    val = m.group(1)
                    if val.endswith('\\'):
                        depends_lines.append(val[:-1].strip())
                    else:
                        depends_lines.append(val.strip())
                        break
            else:
                val = line_stripped
                if val.endswith('\\'):
                    depends_lines.append(val[:-1].strip())
                else:
                    depends_lines.append(val.strip())
                    break
                    
        depends_str = ' '.join(depends_lines)
        if pkg_name_val:
            depends_str = depends_str.replace('$(PKG_NAME)', pkg_name_val)
            depends_str = depends_str.replace('${PKG_NAME}', pkg_name_val)
            
        for token in depends_str.split():
            dep = parse_dep_token(token)
            if dep and dep not in deps:
                deps.append(dep)
                
    return deps

def scan_directory_packages(directory):
    pkg_map = {}
    if not os.path.exists(directory):
        return pkg_map
        
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        if 'Makefile' in files:
            makefile_path = os.path.join(root, 'Makefile')
            try:
                with open(makefile_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                pkgs = extract_packages_from_makefile(content)
                for pkg in pkgs:
                    if pkg not in pkg_map:
                        pkg_map[pkg] = []
                    pkg_map[pkg].append(root)
            except Exception:
                pass
    return pkg_map

def scan_cache_packages(cache_dir):
    return scan_directory_packages(cache_dir)

# Global map to store installed packages
INSTALLED_PKGS = {}

def scan_installed_packages():
    global INSTALLED_PKGS
    pkg_dir = os.path.join(OPENWRT_ROOT, 'package')
    if os.path.exists(pkg_dir):
        print("[INFO] Scanning installed packages in OpenWrt...")
        INSTALLED_PKGS = scan_directory_packages(pkg_dir)
    else:
        INSTALLED_PKGS = {}

def is_already_installed_elsewhere(pkg_name):
    if pkg_name not in INSTALLED_PKGS:
        return False
    custom_path = os.path.normpath(os.path.join(OPENWRT_ROOT, 'package', 'custom', pkg_name))
    for path in INSTALLED_PKGS[pkg_name]:
        norm_path = os.path.normpath(path)
        if not norm_path.startswith(custom_path + os.sep) and norm_path != custom_path:
            return True
    return False

def find_repo_for_package(pkg_name, packages_cfg):
    for repo_name, repo_info in packages_cfg.get('repositories', {}).items():
        if pkg_name in repo_info.get('packages', []):
            return repo_name
    return None

def clone_or_update_repo(repo_name, packages_cfg, force_update=False):
    repo_info = packages_cfg.get('repositories', {}).get(repo_name)
    if not repo_info:
        print(f"[ERROR] Repository '{repo_name}' not defined in packages.yml")
        return False
        
    url = repo_info.get('url')
    if not url:
        print(f"[ERROR] Repository '{repo_name}' has no URL")
        return False
        
    repo_dir = os.path.join(CACHE_DIR, repo_name)
    
    if not os.path.exists(repo_dir):
        print(f"[INFO] Cloning '{repo_name}' from {url}...")
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:
            res = subprocess.run(['git', 'clone', url, repo_name], cwd=CACHE_DIR)
            return res.returncode == 0
        except Exception as e:
            print(f"[ERROR] Failed to clone '{repo_name}': {e}")
            return False
    elif force_update:
        print(f"[INFO] Updating '{repo_name}'...")
        try:
            subprocess.run(['git', 'fetch', '--all'], cwd=repo_dir)
            res = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo_dir, capture_output=True, text=True)
            branch = res.stdout.strip()
            res_reset = subprocess.run(['git', 'reset', '--hard', f'origin/{branch}'], cwd=repo_dir)
            return res_reset.returncode == 0
        except Exception as e:
            print(f"[ERROR] Failed to update '{repo_name}': {e}")
            return False
    return True

def install_via_feeds(pkg_name):
    feeds_script = os.path.join(OPENWRT_ROOT, 'scripts', 'feeds')
    if not os.path.exists(feeds_script):
        return False
    
    try:
        res = subprocess.run([feeds_script, 'install', pkg_name], cwd=OPENWRT_ROOT, capture_output=True, text=True)
        if res.returncode == 0:
            scan_installed_packages()
            if pkg_name in INSTALLED_PKGS:
                return True
    except Exception as e:
        print(f"[ERROR] Error running feeds install: {e}")
    return False

def enable_in_config(pkg_name):
    config_path = os.path.join(OPENWRT_ROOT, '.config')
    if not os.path.exists(config_path):
        return
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        target = f'CONFIG_PACKAGE_{pkg_name}=y'
        unset_target = f'# CONFIG_PACKAGE_{pkg_name} is not set'
        
        found = False
        for i, line in enumerate(lines):
            line_strip = line.strip()
            if line_strip == target:
                found = True
                break
            elif line_strip == unset_target or line_strip.startswith(f'CONFIG_PACKAGE_{pkg_name}='):
                lines[i] = target + '\n'
                found = True
                break
        
        if not found:
            lines.append(target + '\n')
            
        with open(config_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"[INFO] Added CONFIG_PACKAGE_{pkg_name}=y to .config")
    except Exception as e:
        print(f"[ERROR] Failed to update .config: {e}")

def ensure_openwrt_root():
    pkg_dir = os.path.join(OPENWRT_ROOT, 'package')
    if not os.path.exists(pkg_dir):
        print(f"[ERROR] '{OPENWRT_ROOT}' does not appear to be an OpenWrt/ImmortalWrt root directory.")
        print("Please run this command from the root directory of your OpenWrt/ImmortalWrt source tree.")
        sys.exit(1)

def resolve_and_sync(pkg_name, cache_pkg_map, synced_set, packages_cfg):
    if pkg_name in synced_set:
        return
        
    if is_already_installed_elsewhere(pkg_name):
        print(f"[INFO] Package '{pkg_name}' is already installed in OpenWrt (non-custom location). Skipping.")
        return
        
    src_dir = None
    if pkg_name in cache_pkg_map and cache_pkg_map[pkg_name]:
        src_dir = cache_pkg_map[pkg_name][0]
    else:
        repo_name = find_repo_for_package(pkg_name, packages_cfg)
        if repo_name:
            print(f"[INFO] Package '{pkg_name}' is in repository '{repo_name}'. Cloning/updating on demand...")
            if clone_or_update_repo(repo_name, packages_cfg):
                # Update our scanned cache
                cache_pkg_map.update(scan_cache_packages(CACHE_DIR))
                if pkg_name in cache_pkg_map and cache_pkg_map[pkg_name]:
                    src_dir = cache_pkg_map[pkg_name][0]
                    
    if not src_dir:
        # Check all cache dirs (maybe in some repository, but not explicitly in packages.yml list)
        cache_pkg_map.update(scan_cache_packages(CACHE_DIR))
        if pkg_name in cache_pkg_map and cache_pkg_map[pkg_name]:
            src_dir = cache_pkg_map[pkg_name][0]
            
    if not src_dir:
        print(f"[INFO] Package '{pkg_name}' not found in cache. Attempting feeds install...")
        if install_via_feeds(pkg_name):
            print(f"[SUCCESS] Package '{pkg_name}' installed via feeds.")
            return
        else:
            print(f"[WARNING] Package '{pkg_name}' could not be resolved in cache or feeds.")
            return
            
    dest_dir = os.path.join(OPENWRT_ROOT, 'package', 'custom', pkg_name)
    print(f"[INFO] Syncing '{pkg_name}' to 'package/custom/{pkg_name}'...")
    
    if os.path.exists(dest_dir):
        try:
            shutil.rmtree(dest_dir)
        except Exception as e:
            print(f"[ERROR] Failed to remove existing directory '{dest_dir}': {e}")
            return
            
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
    try:
        shutil.copytree(src_dir, dest_dir, symlinks=True, ignore=shutil.ignore_patterns('.git', '.github', '.gitignore'))
    except Exception as e:
        print(f"[ERROR] Failed to copy '{src_dir}' to '{dest_dir}': {e}")
        return
        
    synced_set.add(pkg_name)
    enable_in_config(pkg_name)
    
    # Check dependencies of the synced package
    makefile_path = os.path.join(dest_dir, 'Makefile')
    if os.path.exists(makefile_path):
        try:
            with open(makefile_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            deps = extract_dependencies_from_makefile(content, pkg_name)
            for dep in deps:
                if dep in ('libc', 'libpthread', 'librt', 'libstdcpp', 'kernel', 'sstrip'):
                    continue
                if dep in synced_set:
                    continue
                print(f"[INFO] Resolving dependency '{dep}' for '{pkg_name}'...")
                resolve_and_sync(dep, cache_pkg_map, synced_set, packages_cfg)
        except Exception as e:
            print(f"[WARNING] Error reading/parsing Makefile for dependencies of '{pkg_name}': {e}")

# Command Handlers

def cmd_sync(args, packages_cfg):
    ensure_openwrt_root()
    scan_installed_packages()
    
    cache_pkg_map = scan_cache_packages(CACHE_DIR)
    
    # Determine which packages to sync
    packages_to_sync = args.packages
    if not packages_to_sync:
        # Sync all packages listed in packages.yml
        packages_to_sync = []
        for repo_name, repo_info in packages_cfg.get('repositories', {}).items():
            # First, make sure the repo cache is present
            if not os.path.exists(os.path.join(CACHE_DIR, repo_name)):
                clone_or_update_repo(repo_name, packages_cfg)
            packages_to_sync.extend(repo_info.get('packages', []))
            
    if not packages_to_sync:
        print("[WARNING] No packages configured to sync.")
        return
        
    # Re-scan cache after potential clones
    cache_pkg_map = scan_cache_packages(CACHE_DIR)
    
    synced_set = set()
    for pkg in packages_to_sync:
        resolve_and_sync(pkg, cache_pkg_map, synced_set, packages_cfg)
        
    print(f"\n[SUCCESS] Sync complete. Synced {len(synced_set)} packages: {', '.join(synced_set)}")

def cmd_update(args, packages_cfg):
    repositories = packages_cfg.get('repositories', {})
    if not repositories:
        print("[WARNING] No repositories configured in packages.yml")
        return
        
    success_count = 0
    for repo_name in repositories:
        if clone_or_update_repo(repo_name, packages_cfg, force_update=True):
            success_count += 1
            
    print(f"\n[SUCCESS] Update complete. Successfully updated {success_count}/{len(repositories)} repositories.")

def cmd_clean(args, packages_cfg):
    ensure_openwrt_root()
    custom_dir = os.path.join(OPENWRT_ROOT, 'package', 'custom')
    if os.path.exists(custom_dir):
        print(f"[INFO] Removing '{custom_dir}'...")
        try:
            shutil.rmtree(custom_dir)
            print("[SUCCESS] Clean complete.")
        except Exception as e:
            print(f"[ERROR] Failed to clean '{custom_dir}': {e}")
    else:
        print("[INFO] 'package/custom' does not exist. Nothing to clean.")

def cmd_list(args, packages_cfg):
    repositories = packages_cfg.get('repositories', {})
    if not repositories:
        print("[INFO] No repositories defined in packages.yml")
        return
        
    print("Configured Repositories and Packages:")
    print("=" * 60)
    for repo_name, repo_info in repositories.items():
        url = repo_info.get('url', 'No URL')
        pkgs = repo_info.get('packages', [])
        print(f"{repo_name} ({url})")
        if pkgs:
            for pkg in pkgs:
                print(f"  - {pkg}")
        else:
            print("  (No packages explicitly listed)")
        print("-" * 60)

def cmd_search(args, packages_cfg):
    keyword = args.keyword.lower()
    print(f"Searching for '{keyword}'...")
    print("=" * 60)
    
    # 1. Search in packages.yml config
    found_in_config = False
    repositories = packages_cfg.get('repositories', {})
    for repo_name, repo_info in repositories.items():
        url = repo_info.get('url', '')
        pkgs = repo_info.get('packages', [])
        
        repo_matches = keyword in repo_name.lower() or keyword in url.lower()
        matching_pkgs = [p for p in pkgs if keyword in p.lower()]
        
        if repo_matches or matching_pkgs:
            found_in_config = True
            print(f"Configured Repo: {repo_name} ({url})")
            for p in pkgs:
                if p in matching_pkgs:
                    print(f"  - {p} (matches)")
                else:
                    print(f"  - {p}")
            print("-" * 60)
            
    # 2. Search in Cache (including packages not in packages.yml)
    cache_pkg_map = scan_cache_packages(CACHE_DIR)
    found_in_cache = False
    cache_matches = []
    
    for pkg_name, paths in cache_pkg_map.items():
        if keyword in pkg_name.lower():
            # Check if this package is already listed in packages.yml
            # If not, it's a hidden/helper package in cache
            is_listed = False
            for repo_info in repositories.values():
                if pkg_name in repo_info.get('packages', []):
                    is_listed = True
                    break
            if not is_listed:
                cache_matches.append((pkg_name, paths[0]))
                
    if cache_matches:
        print("Additional Packages found in Cache:")
        for pkg, path in cache_matches:
            rel_path = os.path.relpath(path, SCRIPT_DIR)
            print(f"  - {pkg} (path: {rel_path})")
        print("-" * 60)
        found_in_cache = True
        
    if not found_in_config and not found_in_cache:
        print(f"No packages or repositories matching '{keyword}' were found.")

def cmd_doctor(args, packages_cfg):
    ensure_openwrt_root()
    print("Running OPM Doctor Diagnostics...")
    print("=" * 60)
    
    warnings = 0
    errors = 0
    
    # 1. Scan the whole package tree
    print("[1/4] Scanning OpenWrt packages...")
    pkg_dir = os.path.join(OPENWRT_ROOT, 'package')
    all_pkgs = scan_directory_packages(pkg_dir)
    
    # 2. Check for duplicate packages
    print("[2/4] Checking for duplicate packages...")
    duplicates = {name: paths for name, paths in all_pkgs.items() if len(paths) > 1}
    if duplicates:
        print("\n[WARNING] Duplicate package definitions found:")
        for pkg, paths in duplicates.items():
            print(f"  Package: {pkg}")
            for p in paths:
                rel = os.path.relpath(p, OPENWRT_ROOT)
                print(f"    - {rel}")
            warnings += 1
    else:
        print("  No duplicate package definitions found.")
        
    # 3. Check for feeds overrides / conflicts
    print("[3/4] Checking for feeds overrides and conflicts...")
    custom_dir = os.path.join(OPENWRT_ROOT, 'package', 'custom')
    custom_pkgs = scan_directory_packages(custom_dir)
    
    feeds_conflicts = []
    for pkg in custom_pkgs:
        if pkg in all_pkgs:
            # Check if any path is in feeds
            for path in all_pkgs[pkg]:
                rel_path = os.path.relpath(path, OPENWRT_ROOT)
                if rel_path.startswith('package/feeds/'):
                    feeds_conflicts.append((pkg, rel_path))
                    
    if feeds_conflicts:
        print("\n[WARNING] Local custom packages overriding feeds (can cause conflicts):")
        for pkg, feed_path in feeds_conflicts:
            print(f"  Package '{pkg}' in package/custom overrides feed at '{feed_path}'")
            warnings += 1
    else:
        print("  No conflicts with feeds found.")
        
    # 4. Check Makefile structure & satisfying dependencies for package/custom
    print("[4/4] Checking custom package Makefiles and dependencies...")
    if not os.path.exists(custom_dir):
        print("  No custom packages installed yet. Run sync first.")
    else:
        for pkg, paths in custom_pkgs.items():
            # Verify Makefile parses and find dependencies
            for path in paths:
                makefile_path = os.path.join(path, 'Makefile')
                if not os.path.exists(makefile_path):
                    print(f"  [ERROR] Missing Makefile in {os.path.relpath(path, OPENWRT_ROOT)}")
                    errors += 1
                    continue
                    
                try:
                    with open(makefile_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    deps = extract_dependencies_from_makefile(content, pkg)
                    
                    # Verify each dependency exists in all_pkgs or is a system dependency
                    for dep in deps:
                        if dep in ('libc', 'libpthread', 'librt', 'libstdcpp', 'kernel', 'sstrip'):
                            continue
                        if dep not in all_pkgs:
                            # Check if it exists in packages.yml
                            repo = find_repo_for_package(dep, packages_cfg)
                            if repo:
                                print(f"  [WARNING] Package '{pkg}' depends on '{dep}' which is in config but not yet synced.")
                                warnings += 1
                            else:
                                print(f"  [ERROR] Package '{pkg}' depends on '{dep}' which is missing from OpenWrt package tree.")
                                errors += 1
                except Exception as e:
                    print(f"  [ERROR] Failed to parse Makefile for '{pkg}' at '{os.path.relpath(path, OPENWRT_ROOT)}': {e}")
                    errors += 1
                    
    print("\n" + "=" * 60)
    print(f"Diagnostics complete: {errors} Errors, {warnings} Warnings.")
    if errors > 0:
        print("[STATUS] Doctor found issues that should be addressed before compiling.")
    elif warnings > 0:
        print("[STATUS] Doctor found warnings. System is generally buildable, but check warnings above.")
    else:
        print("[STATUS] Doctor reports all systems nominal! Clean build environment.")

def main():
    parser = argparse.ArgumentParser(description="OpenWrt Package Manager (opm)")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync packages to package/custom")
    sync_parser.add_parser_argument = sync_parser.add_argument
    sync_parser.add_argument("packages", nargs="*", help="Specific packages to sync (optional)")
    
    # Update command
    subparsers.add_parser("update", help="Update cached Git repositories")
    
    # Clean command
    subparsers.add_parser("clean", help="Remove package/custom directory")
    
    # List command
    subparsers.add_parser("list", help="List configured repositories and packages")
    
    # Search command
    search_parser = subparsers.add_parser("search", help="Search for packages")
    search_parser.add_argument("keyword", help="Keyword to search for")
    
    # Doctor command
    subparsers.add_parser("doctor", help="Check build environment for conflicts and dependency issues")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
        
    packages_cfg = load_packages_config()
    
    if args.command == "sync":
        cmd_sync(args, packages_cfg)
    elif args.command == "update":
        cmd_update(args, packages_cfg)
    elif args.command == "clean":
        cmd_clean(args, packages_cfg)
    elif args.command == "list":
        cmd_list(args, packages_cfg)
    elif args.command == "search":
        cmd_search(args, packages_cfg)
    elif args.command == "doctor":
        cmd_doctor(args, packages_cfg)

if __name__ == "__main__":
    main()
