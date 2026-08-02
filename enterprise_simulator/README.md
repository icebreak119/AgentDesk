# Enterprise Business Simulator

本地 HTTP 模拟企业订单与退款系统，供初赛演示使用。它不是生产支付系统，接口形状可以在复赛替换成真实 ERP、订单或支付适配器。

启动：

```powershell
python -m enterprise_simulator.server --port 8770
```

接口：

- `GET /enterprise/orders/{order_id}`：订单查询
- `POST /enterprise/refunds`：退款申请，检查审批令牌、订单金额和幂等键
- `POST /enterprise/refunds/{operation_id}/execute`：退款执行并返回回执
- `GET /enterprise/operations/{operation_id}`：执行结果查询
- `POST /enterprise/refunds/{operation_id}/rollback`：补偿回滚

证据写入 JSONL 时仅保留操作 ID、订单号、金额、状态、幂等键和证据引用，不写客户姓名、会话原文或审批令牌。
