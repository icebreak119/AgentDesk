# PPT 视觉设计说明

## 参考来源

- GitHub: [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)
- 许可证: MIT License
- 查阅日期: 2026-08-02

本项目参考其公开示例中的叙事方法：深色章节页与暖白信息页交替、强主张标题、证据截图成为主视觉、图形化而非堆叠卡片的技术说明。未复制其源代码、模板、图片或示例页面。

## AgentDesk 原创内容

- `build_ppt_assets.py` 以本地代码生成封面主视觉 `assets/agentdesk_control_plane.png`。
- `06_架构图.png` 由本仓库的 `build_arch_diagram.py` 生成。
- `07_系统截图/` 为本仓库本地 Runtime、参考编排器与脱敏 Mock API 的可复现截图。
- `02_方案PPT.pdf` 由 `build_ppt_pdf.py` 生成；页面内容遵循当前初赛可验证边界，不将复赛规划表述为已完成能力。

因此，方案 PDF 不依赖外部素材下载，提交包可离线查看。
