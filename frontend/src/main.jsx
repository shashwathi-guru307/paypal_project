import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

// React.StrictMode is intentionally not used: in development it runs effects
// twice, which would fire the /return capture request twice.
createRoot(document.getElementById("root")).render(<App />);
