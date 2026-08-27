import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Gate } from "./auth";
import { Boundary } from "./Boundary";
import { Dialogs } from "./dialog";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <Gate />
      <Dialogs />
    </Boundary>
  </StrictMode>,
);
