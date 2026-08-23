import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Gate } from "./auth";
import { Boundary } from "./Boundary";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <Gate />
    </Boundary>
  </StrictMode>,
);
