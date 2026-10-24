import { createContext, useContext, useState } from "react";
import BottomSheet from "./BottomSheet";
import SessionSheet from "./SessionSheet";

// App-wide session bottom sheet: any component can call openSession(sid).
const SheetContext = createContext({ openSession() {}, close() {} });

export function SheetProvider({ children }) {
  const [sid, setSid] = useState(null);
  const close = () => setSid(null);
  return (
    <SheetContext.Provider value={{ openSession: setSid, close }}>
      {children}
      <BottomSheet open={!!sid} onClose={close} label={sid ? `Session ${sid}` : ""}>
        {sid && <SessionSheet sid={sid} />}
      </BottomSheet>
    </SheetContext.Provider>
  );
}

export const useSheet = () => useContext(SheetContext);
