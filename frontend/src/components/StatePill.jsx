const LOOK = {
  active: ["good", "Active"], idle: ["bad", "Idle"], warned: ["warn", "Warned"], parking: ["accent", "Parking"],
  parked: ["accent", "Parked"], restoring: ["accent", "Restoring"], queued: ["", "Queued"], free: ["", "Free"],
};

export default function StatePill({ state }) {
  const [cls, label] = LOOK[state] || ["", state];
  return <span className={`pill ${cls} ${state === "active" ? "live" : ""}`}><i />{label}</span>;
}
