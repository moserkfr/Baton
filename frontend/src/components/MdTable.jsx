// Renders the first Markdown table found in `md` (used for park_bench.md).
export default function MdTable({ md }) {
  const rows = (md || "").split("\n").filter((l) => l.startsWith("|") && !/^\|\s*-/.test(l));
  if (!rows.length) return <p className="muted">No data.</p>;
  const cells = (l) => l.split("|").slice(1, -1).map((c) => c.trim());
  const [head, ...body] = rows;
  return (
    <table className="md-table">
      <thead><tr>{cells(head).map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
      <tbody>{body.map((r, i) => <tr key={i}>{cells(r).map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}
