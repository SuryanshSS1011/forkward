/** Where Forkward plugs in: GitHub for the repositories, and the MCP clients most teams use to
 * start a sync. Logos are Simple Icons (CC0), drawn in the ink color through a CSS mask. The
 * connect page covers every other MCP client. */
const assistants: { id: string; name: string; logo?: string }[] = [
  { id: "claude-code", name: "Claude Code", logo: "claude" },
  { id: "cursor", name: "Cursor", logo: "cursor" },
  { id: "vscode", name: "VS Code" },
  { id: "codex", name: "Codex", logo: "openai" },
];

const chip =
  "inline-flex items-center gap-2 rounded-full border border-line bg-raised px-3 py-1.5 text-[14px] font-medium text-ink transition-[border-color,transform] duration-200 ";

function Logo({ name }: { name: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-4 w-4 bg-current"
      style={{ WebkitMask: `url(/logos/${name}.svg) center / contain no-repeat`, mask: `url(/logos/${name}.svg) center / contain no-repeat` }}
    />
  );
}

export function ConnectsTo() {
  return (
    <section className="wrap mb-8" aria-labelledby="connects-title">
      <div className="border-t border-line pt-6">
        <h2 id="connects-title" className="sr-only">What Forkward connects to</h2>
        <dl className="grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:gap-10">
          <div>
            <dt className="t-label text-muted">Your repositories</dt>
            <dd className="mt-2">
              <span className={chip}><Logo name="github" />GitHub</span>
            </dd>
            <dd className="mt-2 text-[14px] text-muted">Opens the pull request and reads your CI.</dd>
          </div>
          <div>
            <dt className="t-label text-muted">Your assistant, over MCP</dt>
            <dd className="mt-2">
              <ul className="flex flex-wrap items-center gap-2">
                {assistants.map((c) => (
                  <li key={c.id}>
                    <span className={chip}>{c.logo && <Logo name={c.logo} />}{c.name}</span>
                  </li>
                ))}
                <li className="px-1 text-[14px] font-medium text-muted">or any MCP client</li>
              </ul>
            </dd>
            <dd className="mt-2 text-[14px] text-muted">Start a sync or a preview from the tools you already use.</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
