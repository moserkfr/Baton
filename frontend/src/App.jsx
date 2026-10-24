import { useEffect, useState } from "react";
import { useRoute } from "./router";
import { useTheme } from "./theme";
import Nav from "./components/Nav";
import Toasts from "./components/Toasts";
import Home from "./pages/Home";
import LiveLab from "./pages/LiveLab";
import Timeline from "./pages/Timeline";
import UserPage from "./pages/UserPage";
import Leaderboard from "./pages/Leaderboard";
import Notifications from "./pages/Notifications";
import Admin from "./pages/Admin";
import Results from "./pages/Results";

function loadMe() {
  try { return localStorage.getItem("gpuq.me") || ""; } catch { return ""; }
}

export default function App() {
  const [page = "", arg] = useRoute();
  const theme = useTheme();
  const [myUser, setMyUserState] = useState(loadMe);
  const setMyUser = (u) => {
    setMyUserState(u);
    try { localStorage.setItem("gpuq.me", u); } catch { /* storage blocked */ }
  };

  useEffect(() => {
    if (myUser && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  }, [myUser]);
  useEffect(() => { window.scrollTo(0, 0); }, [page]);

  let body;
  switch (page) {
    case "lab": body = <LiveLab />; break;
    case "user": body = <UserPage key={arg || myUser} user={arg || myUser} myUser={myUser} setMyUser={setMyUser} />; break;
    case "why": body = <Timeline key={arg} sid={arg} />; break;
    case "leaderboard": body = <Leaderboard />; break;
    case "notifications": body = <Notifications myUser={myUser} />; break;
    case "admin": body = <Admin />; break;
    case "results": body = <Results />; break;
    default: body = <Home />;
  }

  return (
    <>
      <Nav page={page} theme={theme} myUser={myUser} />
      <main>{body}</main>
      <footer className="foot">
        <span className="mono">BATON</span>
        <span className="muted">Parks idle GPU sessions, keeps every tensor, serves the queue fairly.</span>
      </footer>
      <Toasts myUser={myUser} />
    </>
  );
}
