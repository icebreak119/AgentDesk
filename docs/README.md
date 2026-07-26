# GOAI 初赛提交材料（AgentDesk）

赛道：新智基座（Agent Infra）  
作品名称：私域客服自治闭环（AgentDesk）

## 文件清单

| 文件 | 用途 | 提交 zip |
|---|---|---|
| `01_作品简介.txt` | 500 字以内简介 | ✅ 放入 |
| `02_方案PPT.pdf` | 方案 PPT（需自行导出） | ✅ 放入 |
| `03_Agent_Identity清单.md` | Agent Identity 附录 | ✅ 放入 |
| `04_Skill清单.md` | 核心 Skill 清单 | ✅ 放入 |
| `05_多Agent闭环说明.md` | 8 步闭环说明 | ✅ 放入 |
| `06_架构图.png` | 架构图（PPT 导出或截图） | ✅ 放入 |
| `02_方案PPT大纲.md` | PPT 制作参考 | 可选 |
| `README.md` | 本说明 | 可选 |

## 打包命令（PowerShell）

在项目根目录执行：

```powershell
Compress-Archive -Path `
  docs\goai\01_作品简介.txt, `
  docs\goai\02_方案PPT.pdf, `
  docs\goai\03_Agent_Identity清单.md, `
  docs\goai\04_Skill清单.md, `
  docs\goai\05_多Agent闭环说明.md, `
  docs\goai\06_架构图.png `
  -DestinationPath AgentDesk_初赛提交.zip -Force
```

> 注意：`02_方案PPT.pdf` 和 `06_架构图.png` 需先自行制作后放入 `docs/goai/`。

## 官网提交页填写

| 字段 | 建议 |
|---|---|
| 作品名称 | 私域客服自治闭环（AgentDesk） |
| 代码仓库 | 可选，建议放 docs 专用仓库 |
| Demo 链接 | 初赛可选 |
| 作品附件 | `AgentDesk_初赛提交.zip` |

提交规则：每赛段最多 3 次，截止前最后一次成功提交为评审版本。
