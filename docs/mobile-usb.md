# USB 行動網路與 USB Ethernet 可選套件

查核日期：2026-10-24。基準為 chasey `03eb30617955519e35845838c6255ebe687eb717`、Linux 6.12.112；不是對所有同品牌裝置的相容保證。

## 建置方式

`wrt_core/deconfig/fragments/mobile_usb.config` 的新增項目選 `m`：與 ROM 同批編譯成可選 APK，保存到同批 signed feed，需要時才安裝。現有 HiLink 所需 `kmod-usb-net-cdc-ether` 維持 `migration.config` 的 `y`。Kconfig 會自動帶入共同依賴；若原 ROM 已預裝某項依賴，不為了精簡移除它。

`ci/mobile-usb-packages.txt` 是發布前必須存在的 28 項清單，包含 Realtek firmware。只有編譯、signed-index／payload 檢查通過後，才算已產出套件。不能將其他核心 ABI 的 kmod 混入，也不使用 force dependencies。

真 `make defconfig` 後，config gate 透過 `--optional-packages ci/mobile-usb-packages.txt` 確認清單每項均保留為 `m` 或 `y`，包括依賴選出的 `r8152-firmware`；若 Kconfig 丟棄任何項目便立即停止。這不強迫原已預裝的依賴降為 `m`，也不取代最終 signed feed 驗證。

## 行動網路協定

| 裝置暴露的 USB 介面 | 驅動／工具 | 核對依據 |
| --- | --- | --- |
| CDC ECM／HiLink 網卡 | `kmod-usb-net-cdc-ether`；一般用 DHCP | [OpenWrt ECM](https://openwrt.org/docs/guide-user/network/wan/wwan/ethernetoverusb_cdc) |
| RNDIS／部分 Android USB 分享 | `kmod-usb-net-rndis` | [OpenWrt RNDIS](https://openwrt.org/docs/guide-user/network/wan/wwan/ethernetoverusb_rndis)、[手機分享](https://openwrt.org/docs/guide-user/network/wan/smartphone.usb.tethering) |
| NCM／Huawei NCM | `kmod-usb-net-cdc-ncm`、`kmod-usb-net-huawei-cdc-ncm`、`comgt-ncm`、`luci-proto-ncm` | [OpenWrt NCM](https://openwrt.org/docs/guide-user/network/wan/wwan/ethernetoverusb_ncm) |
| QMI | `kmod-usb-net-qmi-wwan`、`kmod-usb-wdm`、`uqmi`、`luci-proto-qmi` | [OpenWrt QMI／MBIM](https://openwrt.org/docs/guide-user/network/wan/wwan/ltedongle) |
| MBIM | `kmod-usb-net-cdc-mbim`、`kmod-usb-wdm`、`umbim`、`luci-proto-mbim` | 同上 |
| AT／序列控制與傳統 PPP | `kmod-usb-serial-option`、`kmod-usb-serial-wwan`、`kmod-usb-acm`、`comgt`、`luci-proto-3g` | 同上；序列埠不等於資料通道 |
| 光碟／儲存預設模式切換與排查 | `usb-modeswitch`、`usbutils`、`picocom` | 先讀 VID:PID、介面描述及實際驅動，不自動送 AT／切模式命令 |

### 型號證據與界線

| 裝置族 | 已查到的證據 | 本次涵蓋／仍待核對 |
| --- | --- | --- |
| 華為 Mate 通訊殼改 | 社區有 RNDIS／ECM／NCM 等改機模式，沒有足以涵蓋所有殼改板的官方 Linux VID:PID 清單 | 編入上述協定套件；實物仍須辨識改板、USB composition 及供電 |
| 移遠 Quectel 模組 | [官方論壇 RM500Q](https://forums.quectel.com/t/rm500q-gl-loading-with-different-drivers-on-2-identical-machines/38214) 有同型模組因 USB mode 不同而分別載入 QMI／MBIM；[Linux driver guide](https://sixfab.com/wp-content/uploads/2020/12/Quectel_LTE5G_Linux_USB_Driver_User_Guide_V2.0.pdf) 說明多協定與序列埠 | QMI／MBIM／ECM／NCM／RNDIS 與 AT 依賴齊備，特定新 VID:PID／韌體仍須實測；不是所有移遠產品共用一種驅動 |
| 中興 ZTE F50 | [OpenWrt issue 16294](https://github.com/openwrt/openwrt/issues/16294) 及[使用紀錄](https://www.cpolar.com/blog/zte-f50-provides-a-tutorial-for-the-cudy-tr3000-256m-router-flashed-with-kwrt-firmware-via-usb-shared-network) 提到 USB 分享及 RNDIS／CDC | 對應標準網卡模式已涵蓋；不同原廠／改版韌體與長時供電穩定度未實物驗收 |
| 飛貓 U20 | [UFI 工具文件 §1.3／§9.7](https://github.com/kanoqwq/UFI-TOOLS/blob/cf83b815efb114ed026f06dc90550845aafbfceb/User_Doc.md) 列 RNDIS／CDC-ECM，但適用描述限定「U20（F50 韌體形態）」；不能倒推所有原廠 U20 的預設模式 | 保留多協定套件；原廠 U20 的 VID:PID、實際資料介面與自動綁定待實物確認 |
| DJI 第一代 Cellular Dongle | [官方手冊](https://dl.djicdn.com/downloads/DJI_Mavic_3/DJI_Cellular_Dongle_LTE_USB_Modem_User_Guide_v1.0.pdf) 說明可接電腦，但沒有完整 Linux 相容表；[OpenWrt 歷史實測](https://lwz322.github.io/2023/07/11/Infrastructure.html) 為 `2ca3:4006`，使用 QMI runtime binding；[另一個 ECM 專案](https://github.com/flxxyz/dji-4g-modem) 的 OpenWrt 測試欄仍未完成 | QMI／ECM／序列依賴均編入。已查 Linux 6.12.112 的 `qmi_wwan.c` 與 `option.c`，沒有找到該 `2ca3:4006` 靜態 ID；實際 chasey patch 與實物仍須核對。不能宣稱插上即用，也不盲目加入持久 `new_id`／自動綁定規則 |

只要缺的是已編出的驅動，可從同批套件安裝；若是新 VID:PID、特殊介面／quirk、專有協定、模組韌體限制，僅增加通用依賴不一定足夠。未取得實物描述前不套用別型號的 AT 指令。

[U20 產品資料](https://m.ithome.com/html/773676.htm) 標示 5V2A、USB 3.0-C；DJI 一代原廠手冊標示 5V1A。這不證明 S20P 的單一 USB 埠能在持續負載時滿足供電，驗收仍需排除重枚舉、掉線及線材因素。DJI 的原廠電腦相容描述指 Windows，Linux／OpenWrt 依上述社區證據評估。

## USB 轉 Ethernet（包含 2.5G）

| 晶片／系列 | 同批套件 | 核對結果 |
| --- | --- | --- |
| Realtek RTL8152／RTL8153／RTL8156／RTL8156B | `kmod-usb-net-rtl8152`、自動依賴 `r8152-firmware` | [固定來源 usb.mk](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/03eb30617955519e35845838c6255ebe687eb717/package/kernel/linux/modules/usb.mk)；[Linux 6.12.112 r8152.c](https://github.com/gregkh/linux/blob/v6.12.112/drivers/net/usb/r8152.c) 包含 `0x8156`、RTL8156A/B firmware 及 `SPEED_2500`；固定來源 [realtek.mk](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/03eb30617955519e35845838c6255ebe687eb717/package/firmware/linux-firmware/realtek.mk) 的 `r8152-firmware` 安裝 `rtl8153*` 與 `rtl8156*`。8152／8153 本身不因此升為 2.5G |
| Aquantia／Marvell AQC111 | `kmod-usb-net-aqc111` | [Linux aqc111.c](https://github.com/gregkh/linux/blob/v6.12.112/drivers/net/usb/aqc111.c) 包含 2.5G／5G；實際 USB ID 仍須匹配 |
| ASIX AX8817x、AX88179／AX88178A | `kmod-usb-net-asix`、`kmod-usb-net-asix-ax88179` | 固定來源有驅動；AX88179 主要為千兆，不能因品牌相同保證新 AX88279 2.5G 型號 |
| Microchip LAN78xx、SMSC75xx／95xx | `kmod-usb-net-lan78xx`、`kmod-usb-net-smsc75xx`、`kmod-usb-net-smsc95xx` | 涵蓋常見百兆／千兆 USB 網卡，不宣稱都是 2.5G |

較新的 RTL8157／新 ASIX 型號沒有在本次證據內完成核實，不依賣場的「2.5G／5G」標示一概保證。採購優先選明確標示 RTL8156B 且可核 VID:PID 的產品。

USB 2.5G 網卡需接到實際協商為 SuperSpeed 的 USB 3.x 埠，才能發揮速率；以 `lsusb -t`、`ethtool` 與區網 iperf3 分別確認 USB bus、Ethernet link 及吞吐。Ethernet 顯示 2.5G 不等於 USB bus 已達 5Gb/s，也不等於實測吞吐已達標。S20P 的 USB 3.0 埠此前尚未用已知正常的 SuperSpeed 裝置驗收，仍須完成該對照。

## 實物驗收順序

1. 插入前後比較 `lsusb`、`lsusb -t`、`dmesg`，保留 VID:PID、介面 class、bus speed、綁定 driver。
2. 僅安裝本 ROM 同批 signed feed 的對應套件；核對核心相依，不關閉簽章或強制版本。
3. 行動網路核對 APN／PIN／USB mode／供電，先單獨測資料介面，再加入 ArkBridge 備援，避免多個撥號管理器爭用控制埠。
4. Ethernet 核對 2.5G full-duplex、雙向區網吞吐、錯包與持續負載；最後測重插、重開機及恢復。

本建置不預設新 WAN、防火牆區域、DHCP server 或 USB WAN 自動切換，避免新增設備即改動現有路由。
