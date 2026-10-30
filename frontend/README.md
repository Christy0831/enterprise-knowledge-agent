# 知序 · React界面

从仓库根目录运行 `python run.py serve --build`，打开 http://127.0.0.1:8765。

前端：React + Vite + Fetch事件流。后端：Python标准库HTTP服务，复用现有Agent、DeepSeek和DashScope配置。密钥仅填写在仓库根目录.env，不放入前端或VITE_变量。

```text
npm ci
npm run dev       # 127.0.0.1:5173；另启动Python 8765后端
npm test          # 事件流解析测试
npm run build
npm run test:e2e  # 会启动测试后端；Windows使用Edge，Linux使用Chromium
```

详见 [界面、流式处理与验证](../docs/09-react-ui-and-streaming.md)。模型增量在finish校验前显示为草稿，通过后展示正式状态及引用。Mock返回执行进度和最终结果。
