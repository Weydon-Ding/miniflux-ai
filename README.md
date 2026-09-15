# miniflux-ai
Miniflux with AI

This project integrates with Miniflux to fetch RSS feed content via API or webhook. It then utilizes large language models (e.g., Ollama, ChatGPT, LLaMA, Gemini) to generate summaries, translations, and AI-driven news insights.

## Features

- **Miniflux Integration**: Seamlessly fetch unread entries from Miniflux or trigger via webhook.
- **Schedule Interval**: Specifies the time interval for requesting the miniflux api.
- **LLM Processing**: Generate summaries, translations, etc. based on your chosen LLM agent.
- **AI News**: Use the LLM agent to generate AI morning and evening news from feed content.
- **Flexible Configuration**: Easily modify or add new agents via the `config.yml` file.
- **Markdown and HTML Support**: Outputs in Markdown or styled HTML blocks, depending on configuration.

<table>
  <tr>
    <td>
      summaries, translations
    </td>
    <td>
      AI News
    </td> 
  </tr>
  <tr>
    <td> 
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://github.com/user-attachments/assets/11c208d9-816a-4c8c-bc00-2f780529e58d">
        <source media="(prefers-color-scheme: light)" srcset="https://github.com/user-attachments/assets/c97e2774-ec10-4acb-bef7-25cf8d43da15">
        <img alt="miniflux AI summaries translations" src="https://github.com/user-attachments/assets/c97e2774-ec10-4acb-bef7-25cf8d43da15" width="400" > 
      </picture>
    </td>
    <td> 
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://github.com/user-attachments/assets/b40f5bdd-d265-4beb-a14c-d39d6624760b">
        <source media="(prefers-color-scheme: light)" srcset="https://github.com/user-attachments/assets/e5985025-15f3-43b0-982b-422575962783">
        <img alt="miniflux AI summaries translations" src="https://github.com/user-attachments/assets/e5985025-15f3-43b0-982b-422575962783" width="400" > 
      </picture>
    </td>
  </tr>
</table>

## Requirements

- Python 3.11+
- Dependencies: Install via `pip install -r requirements.txt`
- Miniflux API Key
- API Key compatible with OpenAI-compatible LLMs (e.g., Ollama for LLaMA 3.1)

## Configuration

The repository includes a template configuration file: `config.sample.yml`. Modify the `config.yml` to set up:

> If using a webhook, enter the URL in Settings > Integrations > Webhook > Webhook URL.
> 
> If deploying in a container alongside Miniflux, use the following URL:
> http://miniflux_ai/api/miniflux-ai.

- **Miniflux**: Base URL and API key.
- **LLM**: Model settings, API key, and endpoint.Add timeout, max_workers parameters due to multithreading
- **AI News**: Schedule and prompts for daily news generation
- **Agents**: Define each agent's prompt, allow_list/deny_list filters, and output style（`style_block` parameter controls whether the output is formatted as a code block in Markdown）.


### 管理配置页面

配置页面默认关闭。需要使用时，在 `config.yml` 中显式启用，并为运行进程设置管理员密码环境变量：

```yaml
admin:
  enabled: true
  username: admin
  password_env: MINIFLUX_AI_ADMIN_PASSWORD
```

重启后，在浏览器中访问现有 Flask 服务的 `/admin/config`，使用 `admin.username` 和密码通过 Basic Auth 认证。密码优先读取 `admin.password_env` 指定的环境变量，也可以使用 `admin.password` 作为回退。通过反向代理访问时应启用 HTTPS，避免明文传输认证凭据。

页面按 **Miniflux、LLM、Agents、AI News、Feeds Status** 分组展示配置，使用 Jinja2 服务端渲染和本地 CSS，无需前端构建、JavaScript 或外部 CDN。

- **Miniflux / LLM 可编辑**：页面读取磁盘中的 `config.yml`，可修改 Miniflux 地址、API key、webhook secret、轮询间隔，以及 LLM provider（`openai` / `gemini`）、地址、API key、model、内容长度上限、timeout、max workers、RPM 和 `extra_params`。
- **其他分组只读**：Agents（固定 `summary` / `translate`）、AI News、Feeds Status 显示当前进程启动时加载的配置，不会随刷新热加载。
- **密钥保留**：三个密钥字段仅回填 `********` 或空值，不发送明文；留空或保持星号会保留原密钥，输入新值才替换。管理员凭据不会显示。URL、提示词和 `llm.extra_params` 等其他字段按原值展示，请勿在其中嵌入密钥。
- **参数校验**：`llm.extra_params` 输入 YAML mapping（如 `temperature: 0.5`），不能是列表或标量，留空保存为 `{}`。数字须满足页面的最小值；轮询间隔留空使用自动间隔，内容长度上限留空不限制；timeout / max workers / RPM 留空分别使用 60 / 4 / 1000。保存复用配置核心的完整必填校验；如果错误指向只读字段，请先在 `config.yml` 修正。
- **错误处理**：无效输入不会写入原配置或备份，页面保留普通字段以便修正。出错后新密钥不会回显，如需更换必须重新输入。保存需要有效的限时表单 token；页面打开超过一小时，请刷新后重新编辑。
- **备份与生效**：有效保存先将旧配置备份到同目录的 `config.yml.bak`（覆盖上一份备份），再尽量保留 YAML 注释和顺序写回配置。成功后会提示：**需要重启 miniflux-ai 容器或进程，配置才会完全生效**；保存和刷新页面都不会热加载运行时配置。

配置目录必须可写，且允许创建备份、临时文件及原子替换 `config.yml`。下面容器示例使用的单文件 bind mount（`./config.yml:/app/config.yml`）可能阻止原子替换；此时网页保存会失败并保留原配置。使用网页编辑时，应将配置放在可写、支持原子替换的目录中；不要通过删除备份或改为非原子写入绕过错误。

## Docker Setup

The project includes a `docker-compose.yml` file for easy deployment:

> If using webhook or AI news, it is recommended to use the same docker-compose.yml with miniflux and access it via container name.

```yaml
services:
    miniflux_ai:
        container_name: miniflux_ai
        image: ghcr.io/qetesh/miniflux-ai:latest
        restart: always
        environment:
            TZ: Asia/Shanghai
        volumes:
            - ./config.yml:/app/config.yml
            # - ./entries.json:/app/entries.json # Provide persistent for AI news

```
Refer to `config.sample.*.yml`, create `config.yml`
To start the services:

```bash
docker-compose up -d
```

## Usage

1. Ensure `config.yml` is properly configured.
2. Run the script: `python main.py`
3. The script will fetch unread RSS entries, process them with the LLM, and update the content in Miniflux.

## Roadmap
- [x] Add daily summary(by title, Summary of existing AI)
  - [x] Add Morning and Evening News（e.g. 9/24: AI Morning News, 9/24: AI Evening News）
  - [x] Add timed summary

## FAQ
<details>
<summary> If the formatting of summary content is incorrect, add the following code in Settings > Custom CSS: </summary>

```
pre code {
    white-space: pre-wrap;
    word-wrap: break-word;
}
```
</details>

<details>
<summary> fetcher: refusing to access private network host "xx.xx.xx.xx" </summary>

Starting with Miniflux 2.2.18, `FETCHER_ALLOW_PRIVATE_NETWORKS=1` must now be enabled to access feeds hosted on a local network. See the [official release notes](https://github.com/miniflux/v2/releases/tag/2.2.18) for details.

</details>

<details>
<summary> level=WARN msg="Unable to send new entries to Webhook" ... client: connection to private network is blocked: host "miniflux-ai" resolves to a non-public IP address" </summary>
  
Starting with Miniflux 2.2.18, `INTEGRATION_ALLOW_PRIVATE_NETWORKS=1` must now be enabled to access third-party integration services hosted on a local network. See the [official release notes](https://github.com/miniflux/v2/releases/tag/2.2.18) for details.

</details>

## Contributing

Feel free to fork this repository and submit pull requests. Contributions and issues are welcome!

<a href="https://github.com/Qetesh/miniflux-ai/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Qetesh/miniflux-ai" />
</a>


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Qetesh/miniflux-ai&type=Date)](https://star-history.com/#Qetesh/miniflux-ai&Date)

## License

This project is licensed under the MIT License.
