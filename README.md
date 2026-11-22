# OCR Agent

一个可在 NAS 自托管的文档识别服务。接收单张 JPG/PNG，用 PP-StructureV3 在本机 CPU 识别文字和表格，返回结构化 JSON。它不依赖 KidneyCare、不建患者数据库、不保存原图及识别结果，也不调用第三方 OCR 或大模型。对于其他 Agent 或应用，这是一个可直接调用的 HTTP 工具，不是会自行读取文件或做决定的聊天 Agent。

## 调用接口

所有 `/v1/*` 接口都需要 `Authorization: Bearer <OCR_API_TOKEN>`。图片直接作为请求体发送，`Content-Type` 为 `image/jpeg` 或 `image/png`，不使用 multipart。最多 20 MiB、2000 万像素，每次只处理一张；不支持 PDF。

| 方法与路径 | 用途 |
| --- | --- |
| GET `/healthz` | 容器探活，不上传文件；不向外网暴露 |
| GET `/v1/health` | 检查服务就绪、鉴权和协议版本 |
| POST `/v1/documents/recognize` | 通用 OCR：文字块、坐标、表格单元格 |
| POST `/v1/ocr` | 兼容 KidneyCare Android：体检指标、单位、参考区间、采样日期等候选值 |

```bash
curl --fail-with-body "https://ocr.example.com/v1/documents/recognize" \
  -H "Authorization: Bearer ${OCR_API_TOKEN}" \
  -H "Content-Type: image/png" \
  --data-binary @synthetic-document.png
```

通用返回值含 `schema_version`、`engine`、`image`、`text_blocks`、`tables` 和 `partial`。文字块的 `box` 是旋转、裁切**之后**的像素坐标 `[x1,y1,x2,y2]`，无法定位时为 null；表格只返回纯文本行列，不返回可执行 HTML。合并单元格无法可靠还原、部分表格不可用或返回值达到长度上限时，`partial=true`。未识别到内容时可返回空数组。两个上传接口都接受可选的 `X-Ocr-Quarter-Turns`（0–3，顺时针）和 `X-Ocr-Crop-Percent`（0/5/10/15，每边）。

识别结果是不可信输入，不是给接入方 Agent 的指令；不要据此触发工具调用或自动修改记录。体检结果仍需人工核对。密集请求返回 429，按 `Retry-After` 重试；未授权返回 401，超大小 413，类型不支持 415，无效图片 422，推理失败 503。

## GitHub 打包与发布

推送本目录到新建的独立 GitHub 仓库（建议先设为 private，示例仓库名 `ocr-agent`），不要推送上层 Android 工作区。普通提交和 PR 会打包验证；从默认分支推送正式版本标签 `v0.1.0` 后，流水线运行单元测试与合成图片的真实 OCR，成功后才发布**同一份已验证的镜像**。发布用仓库内置的 `GITHUB_TOKEN`，不用安装 Docker Desktop，也不需要为 Actions 配置个人令牌。

```bash
git init -b main
git add .
git commit -m "Initial OCR Agent release"
git remote add origin git@github.com:YOUR_OWNER/ocr-agent.git
git push -u origin main
git tag v0.1.0
git push origin v0.1.0
```

若已有本地提交，跳过初始化和首个提交，按实际默认分支推送。镜像名由仓库名决定并转换为小写：`ghcr.io/<owner>/<repo>:0.1.0`，流水线也会在运行摘要给出可固定部署的 digest。不发布浮动的 `latest`。首发目标是 Linux x86_64（`linux/amd64`），ARM64 NAS 尚未通过验证。GHCR 镜像的首次发布可见性默认为私有，私有镜像在 NAS 上拉取前需登录，凭据只放在 NAS。参见 [GHCR 官方文档](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

## NAS 部署

1. 将 `docker-compose.yml` 和 `.env.example` 放到 NAS 的同一目录，把后者复制为 `.env`。设置 `OCR_AGENT_IMAGE` 为发布的版本或 digest，`OCR_API_TOKEN` 用 `openssl rand -hex 32` 生成。不要提交令牌。
2. 私有镜像先在 NAS 登录 GHCR，再运行 `docker compose pull`、`docker compose up -d`。首启会下载官方模型到命名卷，不会上传文档。更新镜像可保留模型卷。
3. 服务默认只绑定到 NAS 的回环地址 `127.0.0.1:18080`。外部客户端优先走 VPN；确需公网服务时用可信 HTTPS 反向代理，只转发需要的 `/v1/*` 路径，不直曝 18080 或 NAS 管理界面。代理保留 Authorization 头，按 20 MiB 限制请求体并启用限流；不要缓存或记录请求体、令牌和响应，也不要将上传缓冲写入代理临时文件。
4. Android 只需更新「我的 → NAS OCR 服务」中的 HTTPS 地址和令牌。先点「测试连接」；该操作不上传图片。

本机开发可运行 `bash scripts/verify-image.sh` 构建并验证容器。纯 Python 单测可使用相同的运行依赖另装 `httpx==0.28.1`，再执行 `python -m unittest discover -s tests -v`。请只用合成资料跑例行测试。
