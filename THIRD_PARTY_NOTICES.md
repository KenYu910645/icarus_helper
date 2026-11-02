# 第三方元件與素材來源

查核日期：2026-11-02（Asia/Taipei）。本文件隨原始碼及 Windows Release 一起提供。各元件的著作權、商標與授權仍屬原權利人；下列授權只適用於對應元件，沒有替 ICARUS Helper 自有程式碼或遊戲素材新增 MIT 等授權。

## Windows 執行元件

| 元件 | 本次建置版本／用途 | 授權與隨附原文 |
| --- | --- | --- |
| CPython | 3.12.10，Python 執行環境與標準函式庫 | [Python 授權](licenses/Python-3.12.10-LICENSE.txt)，包含其 Windows 發行版隨附的第三方條款 |
| PySide6、Shiboken6 | 6.11.1，Qt 的 Python 介面 | 使用其 LGPLv3 選項；[LGPLv3](licenses/Qt-6.11.1/qtbase/LICENSES/LGPL-3.0-only.txt)、[GPLv3](licenses/Qt-6.11.1/qtbase/LICENSES/GPL-3.0-only.txt)；[原套件授權 metadata](licenses/installed-package-metadata.json) |
| Qt | 6.11.1，Core／Gui／Widgets／Network 及封裝使用的影像外掛等 | LGPLv3 或各元件指定條款；[Qt 授權原文與第三方 attribution](licenses/Qt-6.11.1/)，其中上游來源清單可能包含未被本程式載入的選用元件 |
| OpenSSL | 3.0.16，Python 的 `libcrypto-3.dll` | [Apache License 2.0 及原始聲明](licenses/OpenSSL-3.0.16-LICENSE.txt) |
| Mesa llvmpipe、LLVM | Qt 隨附的 `opengl32sw.dll` 軟體繪圖備援 | [Mesa／Khronos／Boost 條款](licenses/Mesa-llvmpipe-LICENSE.txt)、[LLVM 條款](licenses/LLVM-LICENSE.txt) |
| Microsoft Visual C++ Runtime | `MSVCP140*.dll`、`VCRUNTIME140*.dll`，Windows 執行相依 | [Microsoft Visual C++ Runtime 2015–2022 條款](licenses/MSVC-Runtime-2015-2022-LICENSE.txt)，另見 Python 原文中的 Windows binary 條件 |
| PyInstaller | 6.21.0，EXE bootloader 與執行時載入程式 | [GPLv2-or-later 與 Bootloader Exception](licenses/PyInstaller-6.21.0-COPYING.txt) |

Qt 動態函式庫以獨立 DLL 隨附，未修改 Qt、PySide6 或 Shiboken6 的來源。不另加禁止為除錯而修改／替換這些 LGPL 元件或反向工程的限制；替換版本仍須與 Python／Qt ABI 相容。第三方原文的下載來源與 SHA-256 記錄在 [licenses/sources.json](licenses/sources.json)。

對應來源可由原廠取得：

- [CPython 3.12.10](https://www.python.org/downloads/release/python-31210/)。
- [Qt for Python 6.11.1 來源發行目錄（PySide6／Shiboken6）](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/)。
- [Qt Base 6.11.1 原始碼](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtbase-everywhere-src-6.11.1.tar.xz)、[Qt SVG 6.11.1 原始碼](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtsvg-everywhere-src-6.11.1.tar.xz)、[Qt Image Formats 6.11.1 原始碼](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/qtimageformats-everywhere-src-6.11.1.tar.xz)。
- [Qt 6.11.1 其他模組來源目錄](https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/)、[OpenSSL 3.0.16 來源](https://github.com/openssl/openssl/tree/openssl-3.0.16)、[PyInstaller 6.21.0 來源](https://github.com/pyinstaller/pyinstaller/tree/v6.21.0)。

## ICARUS 與地圖素材

- ICARUS 遊戲名稱、金色標誌、原生物品／動物種類圖示及由已安裝遊戲資產整理的資料，權利屬各原權利人。本助手不是官方產品，未由遊戲開發／發行單位背書。助手圖示在原金色標誌加上綠色 H 角標，沒有因此取得原標誌的新授權。
- [Icarus Intel](https://www.icarusintel.com/) 提供目前底圖、社群地穴編號與候選資源位置；各地圖的 `*_public_provenance.json` 保留原來源網址與取回時間。
- 上述來源紀錄是出處說明，**不代表已取得公開再散布授權**。本文件不將第三方圖像或資料改授權為本專案程式碼的授權。若權利人對素材使用有要求，應依其條款處理。

完整資料範圍與推定／未知限制見 [DATA_SOURCES.md](DATA_SOURCES.md)。基地預設只公開使用者指定分享的位置；私人存檔、帳號資料、完整安裝資產與本機診斷不屬於發行內容。

## 開發工具

pytest、Pillow、repak、UAssetAPI 是測試或資料建置工具，沒有因為此來源專案使用它們就成為 APP 的執行需求。其各自授權仍適用於工具本身；Release 未隨附 repak、UAssetAPI、Pillow 或 pytest 執行套件。PyInstaller hooks 的上游授權另保留於 [licenses/PyInstaller-hooks-contrib-LICENSE.txt](licenses/PyInstaller-hooks-contrib-LICENSE.txt)。
