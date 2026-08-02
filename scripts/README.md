# 演示服务脚本

在仓库根目录执行：

```powershell
.\scripts\start_demo.ps1
Start-Process "http://127.0.0.1:8780/"
```

停止全部由脚本启动的服务：

```powershell
.\scripts\stop_demo.ps1
```

默认启动三个本地服务：

| 服务 | 地址 |
|---|---|
| 企业动作模拟器 | `8770` |
| 企业微信 Webhook | `8771` |
| 实时演示页 | `8780` |

进程号和服务日志写入 `tmp/demo_services/`，该目录不进入 Git。
