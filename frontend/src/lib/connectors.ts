/** How to connect Forkward's MCP server to each client. Hosted = the metered HTTPS endpoint, with
 * an API key in a header or, for URL-only apps, OAuth; local = the server on your machine with
 * your own model key. */
export type Client = {
  id: string;
  name: string;
  where: string;
  lang: "bash" | "json" | "toml" | "yaml" | "text";
  hosted: (url: string, key: string) => string;
  local?: string;
  oneClick?: (url: string, key: string) => { label: string; href: string };
  /** Takes only a URL and signs in with OAuth (GitHub through Forkward), so no key is involved. */
  urlOnly?: boolean;
};

const LOCAL_CMD = ["--from", "git+https://github.com/SuryanshSS1011/forkward", "forkward-mcp"];
const localEnv = { LUMINAL_API_KEY: "<your Luminal key>", GENERATOR_MODEL: "deepseek-ai/DeepSeek-V4.1-Flash" };
const json = (v: unknown) => JSON.stringify(v, null, 2);
const bearer = (key: string) => ({ Authorization: `Bearer ${key}` });

export const clients: Client[] = [
  {
    id: "claude-code",
    name: "Claude Code",
    where: "Run in your terminal",
    lang: "bash",
    hosted: (url, key) => `claude mcp add --transport http forkward ${url} \\\n  --header "Authorization: Bearer ${key}"`,
    local: `claude mcp add forkward \\\n  -e LUMINAL_API_KEY=<your Luminal key> \\\n  -e GENERATOR_MODEL=deepseek-ai/DeepSeek-V4.1-Flash \\\n  -- uvx ${LOCAL_CMD.join(" ")}`,
  },
  {
    id: "claude-app",
    name: "Claude (claude.ai and Desktop)",
    where: "Settings → Connectors → Add custom connector, then paste this URL",
    lang: "text",
    urlOnly: true,
    hosted: (url) => url,
    local: json({ mcpServers: { forkward: { command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    where: "Settings → Apps and connectors → add a custom MCP server, then paste this URL",
    lang: "text",
    urlOnly: true,
    hosted: (url) => url,
  },
  {
    id: "cursor",
    name: "Cursor",
    where: "~/.cursor/mcp.json, or use the one-click install",
    lang: "json",
    hosted: (url, key) => json({ mcpServers: { forkward: { url, headers: bearer(key) } } }),
    local: json({ mcpServers: { forkward: { type: "stdio", command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
    oneClick: (url, key) => ({
      label: "Add to Cursor",
      href: `cursor://anysphere.cursor-deeplink/mcp/install?name=forkward&config=${btoa(JSON.stringify({ url, headers: bearer(key) }))}`,
    }),
  },
  {
    id: "vscode",
    name: "VS Code",
    where: ".vscode/mcp.json, or use the one-click install",
    lang: "json",
    hosted: (url, key) => json({ servers: { forkward: { type: "http", url, headers: bearer(key) } } }),
    local: json({ servers: { forkward: { type: "stdio", command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
    oneClick: (url, key) => ({
      label: "Add to VS Code",
      href: `vscode:mcp/install?${encodeURIComponent(JSON.stringify({ name: "forkward", type: "http", url, headers: bearer(key) }))}`,
    }),
  },
  {
    id: "windsurf",
    name: "Windsurf",
    where: "~/.codeium/windsurf/mcp_config.json",
    lang: "json",
    hosted: (url, key) => json({ mcpServers: { forkward: { serverUrl: url, headers: bearer(key) } } }),
    local: json({ mcpServers: { forkward: { command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
  },
  {
    id: "zed",
    name: "Zed",
    where: "settings.json",
    lang: "json",
    hosted: (url, key) => json({ context_servers: { forkward: { url, headers: bearer(key) } } }),
    local: json({ context_servers: { forkward: { command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
  },
  {
    id: "codex",
    name: "OpenAI Codex CLI",
    where: "~/.codex/config.toml, with FORKWARD_KEY exported in your shell",
    lang: "toml",
    hosted: (url, key) => `# export FORKWARD_KEY=${key}\n[mcp_servers.forkward]\nurl = "${url}"\nbearer_token_env_var = "FORKWARD_KEY"\ntool_timeout_sec = 600`,
    local: `[mcp_servers.forkward]\ncommand = "uvx"\nargs = ${JSON.stringify(LOCAL_CMD)}\nenv = { LUMINAL_API_KEY = "<your Luminal key>" }\ntool_timeout_sec = 600\nstartup_timeout_sec = 30`,
  },
  {
    id: "gemini",
    name: "Gemini CLI",
    where: "~/.gemini/settings.json",
    lang: "json",
    hosted: (url, key) => json({ mcpServers: { forkward: { httpUrl: url, headers: bearer(key), timeout: 600000 } } }),
    local: json({ mcpServers: { forkward: { command: "uvx", args: LOCAL_CMD, env: localEnv, timeout: 600000 } } }),
  },
  {
    id: "cline",
    name: "Cline",
    where: "Cline → MCP Servers → Configure",
    lang: "json",
    hosted: (url, key) => json({ mcpServers: { forkward: { type: "streamableHttp", url, headers: bearer(key), autoApprove: ["get_updates", "get_usage", "list_models"] } } }),
    local: json({ mcpServers: { forkward: { command: "uvx", args: LOCAL_CMD, env: localEnv } } }),
  },
];
