SHORT TRACKER - WINDOWS QUICK START / WINDOWS 快速开始
====================================================

ENGLISH
-------
1. Fully extract the ZIP. Do not run the EXE from inside the ZIP viewer.
2. Double-click the single visible "Short Tracker.exe". It starts the embedded
   loopback service and displays the dashboard in one native pywebview/WebView2
   desktop window. No separate browser is required.
3. Click the window X to stop the embedded service safely. If an FCA sync is
   active, the app waits for the import to finish instead of corrupting it.
4. While the app is open, FCA data can sync automatically every 6, 12, or 24
   hours; the default is 6 hours. Closing the window stops this scheduler. The
   app creates no Windows scheduled task and leaves no resident service.
5. Initial/manual/automatic FCA sync and optional Yahoo price requests need
   internet access.
6. On launch, and at most once per 24 hours unless the user explicitly requests
   a fresh check, the app reads public Release metadata only from
   ClydeKingsley/uk-short-tracker on GitHub. It sends no GitHub token and never
   downloads or installs an application update automatically.
7. Frozen-EXE user data is stored under %LOCALAPPDATA%\ShortTracker\data and is
   not inside this program folder. Source mode instead defaults to
   <PROJECT>\data. Upgrading or deleting this program folder does not delete the
   frozen application's database, FCA archive, settings, or caches.
8. The service listens only on 127.0.0.1. Do not expose it through port
   forwarding or a reverse proxy.
9. The desktop window requires Microsoft Edge WebView2 Runtime. Windows 11 and
   most Windows 10 systems already include it; Short Tracker shows an explicit
   error instead of falling back to the obsolete Internet Explorer engine.

This build does not require Python. It is currently unsigned, so Windows may
show "Unknown publisher" or a SmartScreen warning. Verify the published
SHA-256. A checksum verifies bytes but is not a code signature.

Complete third-party licence texts and their hash-bound inventory are under
LICENSES. The overview is THIRD-PARTY-NOTICES.txt.

简体中文
--------
1. 请先“全部解压”，不要直接在 ZIP 压缩包窗口内运行 EXE。
2. 双击唯一可见的“Short Tracker.exe”。它会启动内置的本机服务，并通过
   pywebview/WebView2 在一个原生桌面窗口中显示页面，不需要另开浏览器。
3. 点击窗口右上角 X 即可安全停止内置服务。如果 FCA 同步仍在运行，程序会等待
   导入结束，不会破坏正在写入的数据。
4. 程序打开期间可按 6、12 或 24 小时自动同步 FCA，默认是 6 小时。关闭窗口后
   调度器随即停止；程序不创建 Windows 计划任务，也不留下常驻服务。
5. 首次、手动或自动 FCA 同步以及可选的 Yahoo 价格查询需要联网。
6. 程序启动时会检查 GitHub 公开 Release 元数据；除非用户明确要求重新检查，否则
   24 小时内最多联网一次。唯一来源是 ClydeKingsley/uk-short-tracker，检查不发送
   GitHub token，且 Short Tracker 绝不会自动下载或安装软件更新。
7. 冻结 EXE 的用户数据保存在 %LOCALAPPDATA%\ShortTracker\data，不在程序目录内；
   源码模式默认使用 <项目目录>\data。升级或删除程序目录不会删除冻结版的数据库、
   FCA 归档、设置或缓存。
8. 服务只监听 127.0.0.1，请勿通过端口转发或反向代理对外暴露。
9. 桌面窗口需要 Microsoft Edge WebView2 Runtime。Windows 11 及绝大多数
   Windows 10 已包含它；如果缺失，程序会明确提示，不会退回过时的 IE 引擎。

本版本不需要安装 Python。目前 EXE 没有代码签名，Windows 可能显示
“未知发布者”或 SmartScreen 提示。请核对发布页提供的 SHA-256；校验和只能
验证文件字节，不能替代代码签名。

完整第三方许可证正文及逐文件哈希清单位于 LICENSES，概览见
THIRD-PARTY-NOTICES.txt。

PRIVACY AND SAFETY / 隐私与安全
-------------------------------
- No telemetry, broker login, credential storage, or order execution.
- Never share the whole data folder, database, cache, or unredacted logs.
- This is research software and not investment, legal, tax, or trading advice.

- 没有遥测、券商登录、凭证保存或订单执行功能。
- 不要公开完整 data 目录、数据库、缓存或未经脱敏的日志。
- 本工具只用于研究，不构成投资、法律、税务或交易建议。
