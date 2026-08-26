# Short Tracker：英国公开净空头仓位追踪器

[English](README.md)

[官方网站](https://ukshort.com) ·
[GitHub 仓库](https://github.com/ClydeKingsley/uk-short-tracker) ·
[版本下载](https://github.com/ClydeKingsley/uk-short-tracker/releases)

Short Tracker 是一个本地运行的 Windows 研究工具。它将 FCA 旧制具名净空头
披露与匿名汇总净空头仓位（ANSP）分开保存和展示，可以查看当前做空比例排行、
单只股票的历史做空比例曲线，以及采用同一时间轴的第三方历史股价图；界面支持
中文与英文切换。

> **发布状态：** Short Tracker 已采用 [MIT 许可证](LICENSE)。目前 Windows 发布包
> 未进行代码签名，因此 Windows 可能显示“未知发布者”或 SmartScreen 提示；运行下载
> 文件前请核对 SHA-256 与 Release 说明。维护者门禁记录见
> [公开发布检查表](PUBLICATION-CHECKLIST.md)。

## 普通 Windows 用户：不需要安装 Python

正式面向普通用户的方式是下载 GitHub Release 中的 Windows x64 ZIP；打包的桌面版
支持 x64 架构的 Windows 10 和 Windows 11：

1. 下载 `Short-Tracker-v<版本>-windows-x64.zip` 和 SHA-256 文件；
2. 右键选择“全部解压”，不要直接在 ZIP 压缩包内部运行 EXE；
3. 双击 **Short Tracker.exe**；
4. 唯一可见的 `Short Tracker.exe` 会启动只监听本机的内置服务，健康检查通过后，
   使用 pywebview 和 Microsoft Edge WebView2 在原生桌面窗口中显示页面，不需要
   另开浏览器；
5. 点击窗口右上角的 **X** 即可安全停止内置服务；如果 FCA 同步仍在运行，程序会
   等待同步结束，不会破坏正在导入的数据。

程序打开期间可按 6、12 或 24 小时自动检查并同步 FCA 数据，默认间隔为 6 小时。
该设置不会创建 Windows 计划任务，也不会安装常驻后台服务；关闭窗口后，本地服务和
应用内自动同步调度都会停止。

发布包采用 PyInstaller `onedir`，没有黑色控制台，不使用 UPX，不请求管理员权限，
不安装 Windows 服务，不修改注册表，也不配置开机启动。目前构建没有代码签名，
Windows 仍可能显示“未知发布者”或 SmartScreen 提示。SHA-256 可以验证文件字节，
但不能替代可信代码签名。

从 v0.2.2 开始，桌面外壳使用仅属于本应用的 .NET 配置，因此即使 Windows 把
下载 ZIP 的“来自互联网”标记传递给了解压后的 DLL，经过审查并随发布包提供的托管
程序集仍可正常载入。普通用户不需要安装 Python、移动目录或逐个手工解除 DLL
锁定。发布构建会主动给 `Python.Runtime.dll` 加上同类 Internet 区域标记，并实际
初始化 Edge WebView2 后端作为回归测试。

## 数据位置、升级和卸载

解压目录只存放程序文件；所有可写数据默认位于：

```text
%LOCALAPPDATA%\ShortTracker\data
```

其中可能包含 SQLite 数据库、FCA 原始公开文件、价格缓存、人工确认的 ticker 映射、
应用设置、WebView2 本地界面资料、运行状态和诊断日志。这些内容不会进入源码仓库或 Release ZIP。与此不同，
从源码运行时默认使用 `<项目目录>/data`；源码模式与冻结 EXE 不会暗中共用数据目录。

Short Tracker 会从正式公开仓库
[`ClydeKingsley/uk-short-tracker`](https://github.com/ClydeKingsley/uk-short-tracker)
检查 GitHub Release 元数据，但绝不会自动下载或安装软件更新；经过验证的提示只会把
用户带到官方 Release 页面。成功检查会缓存 24 小时，GitHub 限流响应会触发退避等待。

升级时请先关闭旧版 Short Tracker 并等待它退出，把新 ZIP 解压到新目录，确认新版本
可用后再删除旧程序目录。替换或删除程序目录不会删除 Local AppData，因此正常升级
不会删除数据库、FCA 归档、设置或缓存。若要彻底清除本地数据，请先关闭程序，再手工
删除 `%LOCALAPPDATA%\ShortTracker`。

## 口径边界

工具主要展示：

- 可搜索的 FCA 公司名、ISIN 与已经复核的 ticker 映射；
- 保留 FCA reportable share/ISIN 粒度的当前 ANSP 排行；
- 分开的旧制具名披露历史和 ANSP 历史；
- 制度切换边界，而不是误导性地把两个口径硬接成一条连续线；
- FCA 来源、下载和激活证据；
- 可选的 Yahoo Finance 历史价格与最新观察值；
- 中英文界面。

旧制通常公开单个达到或超过 0.50% 的具名持仓；ANSP 通常汇总达到报告门槛、一般
从 0.20% 起的仓位，但不公开持仓人身份和人数。因此 2026 年 7 月制度切换附近的
跳变可能来自测量口径变化，不等于实际经济空头敞口以同样幅度变化。详见
[方法学](docs/methodology.md)和[数据字典](docs/data-dictionary.md)。

## 网络、隐私和金融安全

Short Tracker：

- 只监听 `127.0.0.1`；
- 首次、手动或应用打开期间的自动同步只下载 FCA 公开披露文件；
- 仅在可选 ticker 搜索和价格查询时访问 Yahoo Finance；
- 程序启动且 24 小时缓存到期时，仅访问 `api.github.com` 检查
  `ClydeKingsley/uk-short-tracker` 的公开 Release 元数据；不发送 GitHub token，
  也不会自动下载或安装更新；
- 关闭桌面窗口后会停止本地服务和 FCA 自动同步，不创建 Windows 计划任务；
- 没有遥测；
- 不连接券商或交易账户；
- 不索取或保存密码、PIN、验证码、API key 或券商凭证；
- 不能创建、修改或取消订单。

提交问题前请阅读 [PRIVACY.md](PRIVACY.md)。不要上传完整 `data/`、SQLite 数据库、
FCA 原始归档、Yahoo 缓存或未经脱敏的日志。

## 从源码运行

源码方式需要 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m short_tracker serve --host 127.0.0.1 --port 8777 --open
```

源码模式默认将数据写入 `<项目目录>/data`（通常就是仓库根目录下的 `./data`），
而不是冻结 EXE 使用的 Local AppData；Git 会忽略整个目录。指定其他数据目录时，
`--data-dir` 必须写在子命令前：

```powershell
.\.venv\Scripts\python.exe -m short_tracker --data-dir '<数据目录>' sync
```

测试与公开树审计：

```powershell
python -m unittest discover -s tests -v
python -m tools.audit_public_tree .
```

## 构建 Windows 发布包

Windows 正式发布构建固定使用 uv 0.11.29 管理的 CPython 3.11.15；原生运行时
指纹及其已复核的许可证来源属于发行证明的一部分：

```powershell
uv python install 3.11.15
uv venv --python 3.11.15 .build-venv
uv pip install --python .\.build-venv\Scripts\python.exe --require-hashes -r .\requirements-build.lock
pwsh -NoLogo -NoProfile `
  -File .\packaging\Build-WindowsRelease.ps1
```

正式构建必须存在真实 `LICENSE`。脚本会运行单元测试、公开树隐私扫描，并生成只有
一个可见 `Short Tracker.exe` 的窗口化 `onedir` 包。解压包冒烟测试覆盖原生 WebView2
窗口生命周期、重复启动、静态资源、点击 X 安全停止、自动同步设置、随机端口和隔离
数据目录，同时确认程序目录在运行前后没有被写入。构建还会把实际 PyInstaller 模块图
和二进制版本与 `LICENSES` 中的完整第三方许可证逐项核对，缺失或被修改的许可材料会
直接阻断构建和 ZIP 校验；最终生成 ZIP、逐文件 manifest 和 SHA-256。

`-AllowMissingLicense` 只能用于内部、不可公开分发的开发预览；这种包会带有醒目标记，
绝不能上传到公开 Release。GitHub 的 tag 工作流在全部门禁通过后只创建草稿
Release，仍需维护者人工检查并发布。

## 风险说明

请在提交代码前阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，安全漏洞报告方式见
[SECURITY.md](SECURITY.md)。数据源或方法学争议应使用专门的数据问题模板，而不是按
安全漏洞处理。

本工具仅用于研究，不是交易系统。公开披露是阈值数据，可能延迟、缺失、追溯修订，
或因制度不同而不可直接比较。Yahoo 端点不是本项目控制的正式行情 API，也不是执行级
数据源。本项目不构成投资、法律、税务或交易建议。
