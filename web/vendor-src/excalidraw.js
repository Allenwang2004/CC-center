/**
 * The entry esbuild bundles into web/vendor/excalidraw/ (see web/vendor.mjs).
 *
 * Excalidraw is a React component. The page has no React and no bundler, so
 * this file is the whole of what it needs to know: mount the component into
 * an element, re-render it with new props, take it down. React itself rides
 * along inside the bundle; nothing here is exposed beyond these few names.
 */
import React from "react";
import { createRoot } from "react-dom/client";
import {
  Excalidraw, getSceneVersion, restore, serializeAsJSON,
} from "@excalidraw/excalidraw";

export { getSceneVersion, restore, serializeAsJSON };

export function mount(el, props) {
  const root = createRoot(el);
  root.render(React.createElement(Excalidraw, props));
  return {
    update: (next) => root.render(React.createElement(Excalidraw, next)),
    destroy: () => root.unmount(),
  };
}
