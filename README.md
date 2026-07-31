# openwrt-package-manager
OpenWrt插件编译管理

## 目录结构：

```text
openwrt-package-manager
├── packages.yml          # 插件列表
├── opm.py                # 主程序
├── README.md
└── cache/                # 自动 clone 的仓库
```

以后在 ImmortalWrt 根目录执行：

```bash
python3 opm.py sync
```

它自动完成：

```
更新仓库
      ↓
同步 package
      ↓
删除旧版本
      ↓
生成 package/custom
```

然后可以直接

```bash
make menuconfig
make -j$(nproc)
```

## 支持命令

```bash
python3 opm.py sync
```

同步所有插件

```bash
python3 opm.py update
```

更新 Git 仓库

```bash
python3 opm.py clean
```

删除 package/custom

```bash
python3 opm.py list
```

查看所有插件

```bash
python3 opm.py search clash
```

搜索插件

```bash
python3 opm.py sync openclash passwall2
```

只同步几个插件

```bash
python3 opm.py doctor
```

检查：

* 重复 package
* Makefile
* 依赖
* feeds 冲突

---

## packages.yml

```yaml
repositories:
  openclash:
    url: https://github.com/vernesong/OpenClash.git
    packages:
      - luci-app-openclash

  passwall:
    url: https://github.com/xiaorouji/openwrt-passwall.git
    packages:
      - luci-app-passwall

  passwall2:
    url: https://github.com/xiaorouji/openwrt-passwall2.git
    packages:
      - luci-app-passwall2

  homeproxy:
    url: https://github.com/immortalwrt/homeproxy.git
    packages:
      - luci-app-homeproxy

  kiddin9:
    url: https://github.com/kiddin9/openwrt-packages.git
    packages:
      - luci-app-aria2
      - luci-app-cloudflarespeedtest

  small-package:
    url: https://github.com/kenzok8/small-package.git
    packages:
      - luci-app-control-timewol
      - luci-app-control-webrestriction
      - luci-app-control-weburl
      - luci-app-timecontrol
      - luci-app-momo
      - luci-app-nekobox
      - luci-app-nikki
      - luci-app-oaf
      - luci-app-wolplus
      - luci-app-vlmcsd
      - luci-app-tcpdump
      - luci-app-quickstart
      - luci-app-turboacc
      - luci-app-ikoolproxy
      - luci-app-ssr-plus
      - luci-app-store
      - luci-app-openlist
      - luci-app-lucky
```

## 核心功能与运行机制

`opm` (OpenWrt Package Manager) 是一款专为 OpenWrt/ImmortalWrt 固件编译设计的第三方插件管理工具。它通过自动化管理源码克隆、依赖解析、包冲突检测、配置自动生成等流程，极大简化了插件的编译和维护工作。

### 1. 自动功能详解
* **按需/增量下载源码**：克隆远程仓库到本地缓存（`cache/` 目录），下次运行优先从缓存同步，显著加快同步速度。
* **自定义存放路径**：自动清空旧包，并克隆/拷贝所需插件的源目录至 `package/custom/<package_name>`，避免手工复制的出错率。
* **智能解析 Makefile**：能够自动解析 package 目录下的 `Makefile`，甚至支持读取 LuCI 专用变量 `LUCI_DEPENDS`。
* **递归补齐依赖**：当目标插件依赖其他包时：
  1. 优先尝试从本项目的 `packages.yml` 其它仓库中提取；
  2. 其次尝试自动在缓存中已下载的其它仓库里查找；
  3. 仍未找到则自动调用 `./scripts/feeds install` 从 OpenWrt 官方源/第三方源安装。
* **自动注入 .config**：同步完插件后，自动在编译根目录的 `.config` 文件中追加/修正 `CONFIG_PACKAGE_<name>=y`，无需手动在 `menuconfig` 中逐个勾选。
* **一键环境诊断 (`doctor`)**：全面检查编译树冲突，检测是否有重名 Package、本地 custom 包是否覆盖了 feeds 包，以及检查当前 Makefile 语法与缺失的编译依赖。

### 2. 高级技术设计
* **零依赖运行**：在宿主环境未安装 `PyYAML` 库时，`opm.py` 会自动降级使用内置实现的简易 YAML 解析器，保证在各种极简的 Linux 编译容器内均能开箱即用。
* **变量自动解析**：完美支持 Makefile 中 `$(PKG_NAME)` 和 `${PKG_NAME}` 等动态变量的替换和识别。
* **防止环境污染**：在文件复制时，自动过滤 `.git`、`.github` 和 `.gitignore` 等版本控制文件，保持 OpenWrt 编译树干净整洁，并防止在 Windows/NTFS 宿主上因 Git 特殊权限文件导致删除报错。

---

## 详细使用指南

请将 `opm.py` 和 `packages.yml` 放置在 **OpenWrt/ImmortalWrt 源码根目录** 下运行。

### 1. 查看配置的插件列表
```bash
python3 opm.py list
```
* **作用**：解析并输出 `packages.yml` 中配置的所有 Git 仓库、URL 以及仓库下定义的受管理包。

### 2. 搜索插件
```bash
python3 opm.py search <keyword>
```
* **作用**：在配置中或已克隆的缓存目录中模糊搜索包含关键词的插件。对于未明示在 `packages.yml` 中但已被带入缓存的辅佐包也能轻松搜到。
* **示例**：`python3 opm.py search passwall`

### 3. 同步插件 (最常用)
```bash
python3 opm.py sync [pkg1 pkg2 ...]
```
* **作用**：
  * 如果**未指定**插件名称：同步 `packages.yml` 中定义的所有仓库与插件。
  * 如果**指定**了一个或多个插件名称：仅拉取/同步指定的插件及其递归依赖项。
* **流程**：自动完成：`克隆或更新仓库 -> 拷贝至 package/custom/ -> 解析依赖并自动补齐 -> 将 CONFIG_PACKAGE_xxx=y 写入 .config`。

### 4. 升级所有插件仓库
```bash
python3 opm.py update
```
* **作用**：强制对 `packages.yml` 配置的所有 Git 仓库执行远程 `fetch`，并将本地缓存重置（`reset --hard`）到最新的远程分支，以便在下次运行 `sync` 时同步最新代码。

### 5. 清理自定义包
```bash
python3 opm.py clean
```
* **作用**：安全地删除 OpenWrt 源码树下的整个 `package/custom` 目录。

### 6. 编译环境健康体检
```bash
python3 opm.py doctor
```
* **作用**：对当前 OpenWrt 编译树进行诊断，检测以下问题并输出健康报告：
  - **重复包冲突**：是否存在多个 Makefile 定义了同一个 Package 名。
  - **Feeds 覆盖冲突**：在 `package/custom` 中存放的本地包是否与 `package/feeds/` 重合，提示覆盖风险。
  - **Makefile 解析错误**：是否存在 Makefile 缺失或解析失败。
  - **缺失依赖**：检测 `package/custom` 下的包所依赖项是否未能成功解析或未存在于 OpenWrt 中。


