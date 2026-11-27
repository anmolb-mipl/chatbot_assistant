import React from "react";
import { createRoot } from "react-dom/client";
import AgentConsole from "./AgentConsole";
import CustomerChat from "./CustomerChat";
import "./styles.css";

const isAgent = location.pathname.startsWith("/agent");

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{isAgent ? <AgentConsole /> : <CustomerChat />}</React.StrictMode>
);
