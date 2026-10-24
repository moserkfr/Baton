import { dur, short } from "../format";

// "Saved you a wait" receipts: a parked session's slot went to someone in the queue.
export default function Receipts({ receipts }) {
  if (!receipts.length) return <p className="empty">Receipts appear when a parked session's slot goes to someone in the queue.</p>;
  return (
    <div className="receipts">
      {receipts.slice().reverse().map((r, i) => (
        <div key={i} className="receipt">
          <div className="eyebrow">{r.open ? "Open · still counting" : "Receipt"}</div>
          <p>
            <b>{short(r.beneficiary)}</b> got the GPU <b>{short(r.owner)}</b> left idle.
          </p>
          <div className="receipt-row"><span>Waited</span><span className="num">{dur(r.waited_s)}</span></div>
          <div className="receipt-row total"><span>Wait saved</span><span className="num">≥ {dur(r.saved_s)}</span></div>
        </div>
      ))}
    </div>
  );
}
