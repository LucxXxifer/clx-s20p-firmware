> **CLX-S20P 固件線**：GitHub `main` 是 chasey ImmortalWrt 25.12 的每日上游候選編譯線；每日 04:20（Asia/Shanghai）檢查，僅在上游有新提交時建置，且只有全部差異、安全、套件與簽章檢查通過才建立 prerelease。公開庫使用 GitHub-hosted 標準 Linux runner，無須付費會員或本機 AI 任務。
>
> **固定下載版 A（10/17）**：[GitHub release 與下載](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-candidate-40095658212-1)／[固定來源分支](https://github.com/LucxXxifer/clx-s20p-firmware/tree/s20p-a-10-17)。此 release 保留原成品、校驗碼及說明；rolling `main` 不會改寫它。既有 B 候選也保留：[B 25.12 candidate](https://github.com/LucxXxifer/clx-s20p-firmware/releases/tag/s20p-b-25.12-candidate-40420056051-1)。
>
> 每日流程、候選發布規則與驗收限制見 [README_AUTOBUILD.md](README_AUTOBUILD.md)。原作者的一般編譯說明保留如下。

---

# 编译指南

本仓库用于按设备配置自动拉取 OpenWrt / ImmortalWrt / LiBwrt 源码、应用自定义补丁与软件包配置，并输出固件到 `firmware/` 目录。

## 1. 环境准备

推荐使用 Ubuntu LTS 或其他主流 Linux 发行版。OpenWrt 编译对磁盘空间、内存和文件系统大小写敏感性有要求，建议预留充足磁盘空间并在原生 Linux 文件系统中编译。

## 2. 安装编译依赖

```bash
sudo apt -y update
sudo apt -y full-upgrade
sudo apt install -y dos2unix libfuse-dev
sudo bash -c 'bash <(curl -sL https://build-scripts.immortalwrt.org/init_build_environment.sh)'
```

容器构建还需要已安装并可正常运行的 Docker。

## 3. 获取源码

```bash
git clone https://github.com/dqsq2e2/wrt_release.git
cd wrt_release
```

## 4. 编译用法

### 交互式选择

直接运行脚本会列出当前 `wrt_core/compilecfg/*.ini` 与 `wrt_core/deconfig/*.config` 同时存在的设备配置，并提示选择构建模式：

```bash
./build.sh
```

### 直接指定设备

```bash
./build.sh <设备配置名> [debug|container|container_debug|config_preview]
```

构建模式说明：

| 模式 | 命令示例 | 说明 |
| --- | --- | --- |
| 默认 | `./build.sh x64_immwrt` | 拉取源码、应用配置、下载依赖并完整编译固件。 |
| `debug` | `./build.sh x64_immwrt debug` | 执行到 `make defconfig` 后停止，用于检查配置，不产出固件。 |
| `container` | `./build.sh x64_immwrt container` | 使用 Docker 容器执行完整构建，减少本机环境差异。 |
| `container_debug` | `./build.sh x64_immwrt container_debug` | 在 Docker 容器中执行 debug 流程并进入交互 shell。 |
| `config_preview` | `./build.sh x64_immwrt config_preview` | 只预览配置片段组合，不拉取源码、不写构建目录。 |

可通过环境变量临时追加或移除配置片段：

```bash
ADD_CONFIG_FRAGMENTS=docker_deps ./build.sh gemtek_w1701k_immwrt config_preview
REMOVE_CONFIG_FRAGMENTS=proxy ./build.sh x64_immwrt config_preview
```

原始 `wrt_release` 的 GitHub Actions 手动构建曾提供 `add_fragments` 与 `remove_fragments` 输入项；本仓库現行的 S20P rolling workflow 不會透過這些輸入改動 package 選擇。

编译完成后，脚本会从 `<BUILD_DIR>/bin/targets/` 收集固件文件到仓库根目录的 `firmware/`。每次完整构建前会清理旧的目标固件文件，`firmware/Packages.manifest` 会被移除。

### 自动云编译

本仓库現行的自動編譯是上方說明的 S20P rolling workflow：每日追蹤 chasey 25.12，僅在精確上游 SHA 前進時執行 build，通過全部 gates 後發 prerelease。原多設備 `upstream_watch`、`build_wrt`、`release_wrt` 與維護 workflow 已移到 `docs/upstream-workflows/` 並改為 `.disabled` 保存，不會在 GitHub Actions 中執行。

## 5. 支持设备

设备配置名来自 `wrt_core/compilecfg/` 和 `wrt_core/deconfig/` 中同名文件。当前支持：

| 厂商 / 平台 | 设备 | 配置名 |
| --- | --- | --- |
| 京东云 | 雅典娜(02)、亚瑟(01)、太乙(07)、AX5(JDC版) | `jdcloud_ipq60xx_immwrt` |
| 京东云 | 雅典娜(02)、亚瑟(01)、太乙(07)、AX5(JDC版) - LiBwrt | `jdcloud_ipq60xx_libwrt` |
| 京东云 | 百里 / AX6000 | `jdcloud_ax6000_immwrt` |
| 京东云 | 百里 / AX6000 - Linux 6.18 / Honk | `jdcloud_ax6000_immwrt_honk` |
| CLX | S20L（AX4200 WiFi） | `S20L-WIFI-YES` |
| CLX | S20P（AX6000 WiFi） | `S20P-WIFI-YES` |
| CLX | S20L、S20M、S20P（移除 WiFi） | `S20-WIFI-NO` |
| 阿里云 | AP8220 | `aliyun_ap8220_immwrt` |
| 阿里云 | AP8220 - LiBwrt | `aliyun_ap8220_libwrt` |
| Linksys | MX4200v1、MX4200v2、MX4300 | `linksys_mx4x00_immwrt` |
| Link | NN6000v2 | `link_nn6000v2_immwrt` |
| 奇虎 | 360v6 | `qihoo_360v6_immwrt` |
| 红米 | AX5 | `redmi_ax5_immwrt` |
| 红米 | AX6 | `redmi_ax6_immwrt` |
| 红米 | AX6 - LiBwrt | `redmi_ax6_libwrt` |
| 红米 | AX6000 | `redmi_ax6000_immwrt21` |
| CMCC（中国移动） | RAX3000M | `cmcc_rax3000m_immwrt` |
| MediaTek / Filogic | 67 个设备 Profile（保留 WiFi） | `MEDIATEK-WIFI-YES` |
| MediaTek / Filogic | S20L、S20M、S20P、EX5700（移除 WiFi） | `MEDIATEK-WIFI-NO` |
| 斐讯 | N1 | `n1_immwrt` |
| 兆能 | M2 | `zn_m2_immwrt` |
| 兆能 | M2 - LiBwrt | `zn_m2_libwrt` |
| Gemtek | W1701K | `gemtek_w1701k_immwrt` |
| x86 | X64 | `x64_immwrt` |

示例：

```bash
./build.sh jdcloud_ipq60xx_immwrt
./build.sh aliyun_ap8220_libwrt
./build.sh redmi_ax6_libwrt container
./build.sh S20L-WIFI-YES
./build.sh S20P-WIFI-YES
./build.sh S20-WIFI-NO config_preview
./build.sh jdcloud_ax6000_immwrt_honk
```

## 6. 配置来源

每个设备由两类文件共同定义：

- `wrt_core/compilecfg/<设备配置名>.ini`：定义源码仓库、分支、构建目录、默认配置片段、可选提交哈希和容器 SDK 镜像。
- `wrt_core/deconfig/<设备配置名>.config`：定义 OpenWrt 目标平台、设备和软件包配置。

不同设备会使用不同上游源码，例如 `VIKINGYFY/immortalwrt`、`immortalwrt/immortalwrt`、`LiBwrt/openwrt-6.x`、`padavanonly/immortalwrt-mt798x` 或本仓库维护的特定分支。`BUILD_TARGET_SDK` 未配置时，容器构建默认使用 `immortalwrt/sdk:openwrt-25.12`。

`S20L-WIFI-YES`、`S20P-WIFI-YES` 和 `S20-WIFI-NO` 均使用 `chasey-dev/immortalwrt-mt798x-rebase` 的 `25.12` 分支。两个 WiFi 配置分别只构建 S20L（AX4200）和 S20P（AX6000），避免不同无线规格共用全局 SKU；无 WiFi 配置继续一次构建 S20L、S20M、S20P。三项配置均保留 `docker_deps,honk` 默认片段，并独立监听上游变更、记录成功构建标记和发布固件。

`jdcloud_ax6000_immwrt_honk` 使用 `VIKINGYFY/immortalwrt` 的 `owrt` 分支，目标为 `jdcloud_re-cp-03`，通过 `EXPECTED_KERNEL=6.18` 检查内核主次版本，构建目录为 `immortalwrt-honk/`。原来的 `jdcloud_ax6000_immwrt` 继续使用 6.12 厂商驱动，两者独立保留。

此配置的应用选择沿用 `jdcloud_ax6000_immwrt.config` 与公共配置，将厂商 WiFi/HNAT 组件换为 mac80211 WiFi 和原生 PPE/nft flowtable，去掉只适用于厂商驱动的 eQoS/Turbo ACC 插件，通过 LuCI 防火墙设置管理流量卸载，并追加 `honk` 片段。参考 `kwrum1/honk-ImmortalWRT-CI`，从 `kenzok8/openwrt-honk` 同步 honk 核心与 luci-app-honk（含 Doona 界面），从 `kenzok8/wall` 同步 v2ray-geodata，为 Honk 提供 GeoIP/GeoSite 数据。核心使用软件包指定的 AArch64 musl 发行包；构建前从同仓库的 `staging` 发布同步 `generated-stage.mk` 和 `generated-provenance.json`，校验核心提交一致并使用发布清单中的 SHA256。若 `main` 的核心版本与发布清单不同，则从 `main` 历史中选择最近一个核心提交匹配、Doona 资源已发布且发布哈希一致的完整快照，同时同步 honk 与 luci-app-honk，再执行相同校验；找不到匹配快照或清单异常时停止构建。BTF 直接编入本机内核，不依赖外置 BTF 文件。Honk 保留软件包默认的未初始化、未启用状态，刷机后在「服务 → Honk」中初始化并配置订阅和分流后启动；不预置旁路由 IP、上游网关或订阅。

此配置还预装 `luci-app-fullconenat-sonic`，使用源码内置的 Sonic 全锥 NAT 管理界面，不混用旧版独立 fullconenat 内核模块。

构建时会按顺序组合配置：

1. 设备专用 `.config`
2. `compile_base.config`
3. `wrt_core/deconfig/fragments/<name>.config` 中的有效配置片段

默认片段由 `compilecfg/*.ini` 的 `CONFIG_FRAGMENTS` 指定：

- `proxy` 选择代理相关软件包，是否默认包含以各设备的 `CONFIG_FRAGMENTS` 为准。
- `honk` 选择 honk、luci-app-honk、GeoIP/GeoSite 数据和 eBPF/BTF 依赖，并控制对应 custom_feed 源码同步与配置校验；默认由 `jdcloud_ax6000_immwrt_honk` 使用。可通过 `ADD_CONFIG_FRAGMENTS=honk` 追加到兼容的 AArch64/x86_64 配置，或通过 `REMOVE_CONFIG_FRAGMENTS=honk` 移除。
- `daed` 片段保留为可选项，选择 daed、luci-app-daede 和 eBPF/BTF 依赖，按需通过 `ADD_CONFIG_FRAGMENTS=daed` 使用。
- IPQ60xx / IPQ807x 设备默认额外包含 `nss`。
- 只有已显式选择 Dockerman 或明确适合运行 Docker 的设备默认包含 `docker_deps`，统一选择 `luci-app-dockerman`、`docker-compose` 及运行依赖，避免 NAND 空间紧张或无 USB 设备被默认加入 Docker 软件包。

`ADD_CONFIG_FRAGMENTS` 会在默认片段后追加，`REMOVE_CONFIG_FRAGMENTS` 会从最终片段中移除对应配置片段。移除只表示“不追加这个 fragment”，不会反向修改设备 `.config` 或 `compile_base.config` 中已经写明的配置。

Dockerman 源码更新和 Docker nftables 兼容补丁同样以最终生效的 `docker_deps` 片段为开关：包含该片段时执行，不包含时跳过。因此通过 `ADD_CONFIG_FRAGMENTS=docker_deps` 可以临时加入 Docker 软件包并启用补丁，通过 `REMOVE_CONFIG_FRAGMENTS=docker_deps` 可以不再追加 Docker 软件包并禁用补丁。单独调用 `wrt_core/update.sh` 时，Docker 更新和补丁默认关闭，由构建入口传入片段解析结果。

## 7. 三方插件

三方插件主要通过 feeds 机制加入，其中 small-package 源自：

```text
https://github.com/kenzok8/small-package.git
```

相关增删和同步逻辑位于 `wrt_core/update.sh` 编排的 `wrt_core/modules/` 静态阶段。配置片段只选择 Kconfig，不负责 clone 仓库、修改 feeds 或安装 feeds。

## 8. 项目结构说明

- `build.sh`：主编译入口，负责设备选择、模式选择、配置组合、容器构建和固件收集。
- `firmware/`：完整构建后的固件输出目录，由脚本自动创建和刷新。
- `wrt_core/build_container.sh`：容器内构建入口。
- `wrt_core/update.sh`：源码更新、feeds 调整、软件包同步和补丁应用主流程。
- `wrt_core/pre_clone_action.sh`：GitHub Actions 预克隆辅助脚本。
- `wrt_core/ci/detect_upstream_changes.sh`：读取设备源码元数据、计算上游与配置指纹并生成自动构建矩阵。
- `wrt_core/compilecfg/`：设备构建元信息 `.ini`。
- `wrt_core/deconfig/`：设备和共享默认配置 `.config`。
- `wrt_core/deconfig/fragments/`：可组合配置片段。
- `wrt_core/modules/`：模块化脚本，包括仓库准备、网络重试、feeds/custom_feed、源码修正、LuCI 修正、服务修正、验证、Docker、CUPS 等静态职责模块。
- `wrt_core/patches/`：补丁、默认设置、Wi-Fi 初始化、NSS 诊断、PBR 规则和其他构建时注入文件。
- `.github/workflows/upstream_watch.yml`：定时检测五个重点配置的上游源码变更并自动发布。
- `.github/workflows/ci_maintenance.yml`：定期清理旧缓存、旧工作流记录和旧 Release。

## 9. OAF（应用过滤）功能使用说明

使用 OAF（应用过滤）功能前，需先完成以下操作：

1. 打开系统设置 → 启动项 → 定位到「appfilter」
2. 将「appfilter」当前状态从已禁用更改为已启用
3. 完成配置后，点击启动按钮激活服务
