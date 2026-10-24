import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { StatusProvider } from "./status";
import { SheetProvider } from "./components/sheet";
import App from "./App";
import "./styles/base.css";
import "./styles/components.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <StatusProvider>
      <SheetProvider>
        <App />
      </SheetProvider>
    </StatusProvider>
  </StrictMode>,
);
