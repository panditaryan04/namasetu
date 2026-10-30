export function HierarchyBreadcrumb({ path }: { path: string[] }) {
  return (
    <nav aria-label="ICD-11 hierarchy" className="text-xs text-slate-600 dark:text-slate-400">
      <ol className="flex flex-wrap items-center gap-1">
        {path.map((p, i) => (
          <li key={`${p}-${i}`} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden="true">›</span>}
            <span className={i === path.length - 1 ? "font-medium text-slate-800 dark:text-slate-200" : ""}>
              {p}
            </span>
          </li>
        ))}
      </ol>
    </nav>
  );
}
