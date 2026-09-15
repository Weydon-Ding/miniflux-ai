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


### 配置管理页面

配置页面默认关闭。需要使用时，在 `config.yml` 中显式启用，并为运行进程设置管理员密码环境变量：

```yaml
admin:
  enabled: true
  username: admin
  password_env: MINIFLUX_AI_ADMIN_PASSWORD
```

重启后，在浏览器中访问现有 Flask 服务的 `/admin/config`，使用 `admin.username` 和密码通过 Basic Auth 认证。密码优先读取 `admin.password_env` 指定的环境变量，也可以使用 `admin.password` 作为回退。通过反向代理访问时应启用 HTTPS，避免明文传输认证凭据。

页面按 **Miniflux、LLM、Agents、AI News、Feeds Status** 分组展示核心配置，Agents 展示固定的 `summary` 和 `translate`。三个密钥字段（Miniflux API key、webhook secret、LLM API key）仅显示固定掩码或“未设置”，不发送明文；管理员凭据不会显示。提示词、URL 和 `llm.extra_params` 等其他配置按原值展示，请勿在其中嵌入密钥。

页面使用 Jinja2 服务端渲染和本地 CSS，无需前端构建、JavaScript 或外部 CDN。**Miniflux、LLM、Agents 目前只读**，显示当前进程启动时加载的配置；**AI News 和 Feeds Status 可编辑**，显示磁盘 `config.yml` 中的待生效值：

- AI News：服务地址、生成时间（每行一个 `HH:MM`，例如 `07:30` 和 `18:00`），以及问候、摘要、分类三个提示词。生成时间留空可取消定时生成；设置生成时间后，三个提示词均必填。
- Feeds Status：启用/禁用、服务地址及单个检查时间（`HH:MM`，例如 `09:00`）。
- 时间采用 24 小时制，范围为 `00:00`–`23:59`。校验失败时显示错误并保留输入，不覆盖原配置或备份。其他已有必填配置缺失时，也会提示；只读项需要先在 `config.yml` 中补全。

点击“保存配置”后，后端复用配置编辑核心校验，在覆盖前将旧配置备份到同目录的 `config.yml.bak`，并尽量保留 YAML 注释和顺序。进程需要拥有配置目录、配置文件和备份文件的写入权限；容器部署建议挂载可写目录，单文件 bind mount 可能阻止原子替换。备份包含密钥，请与原配置一样妥善保护。

保存接口同样要求 Basic Auth，并检查页面携带的 CSRF token；进程重启后请刷新旧页面再保存。保存只写入配置文件，**需要重启 miniflux-ai 容器或进程，配置才会完全生效**；刷新页面可查看待生效值，但不会热加载运行配置。

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
