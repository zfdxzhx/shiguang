import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../app/globals.css";
import { DrawingReviewApp } from "../app/drawing-review-app";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <DrawingReviewApp />
  </StrictMode>,
);
