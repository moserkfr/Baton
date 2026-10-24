// Mono eyebrow, a headline whose second clause is grey, and a lead paragraph.
export default function PageHeader({ eyebrow, title, soft, children }) {
  return (
    <div className="page-head">
      {eyebrow && <div className="eyebrow"><span className="dot" />{eyebrow}</div>}
      <h1 style={{ marginTop: eyebrow ? 14 : 0 }}>{title}{soft && <> <span className="soft">{soft}</span></>}</h1>
      {children && <p>{children}</p>}
    </div>
  );
}
