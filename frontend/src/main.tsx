import React from "react";
import { createRoot } from "react-dom/client";
import DemoApp from "./App";
import LiveApp from "./LiveApp";
import { apiMode } from "./api/client";
import "./globals.css";
import "./live.css";
const App = apiMode === 'demo' ? DemoApp : LiveApp;
createRoot(document.getElementById("eva-root")!).render(<React.StrictMode><App /></React.StrictMode>);
