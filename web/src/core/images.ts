/**
 * cc://image/<id> to something an <img> can show.
 *
 * The page never sees the bucket. Each picture is fetched once through the
 * server, turned into a data: URL, and remembered for the life of the page --
 * a journal entry is repainted on every keystroke in split view, and the
 * pictures in it must not be fetched again each time.
 */

import { api } from "./api.js";

const resolved = new Map<string, Promise<string>>();

/** The id inside a cc://image/ url, or null for anything else. */
export const imageId = (url: string): string | null => {
  const m = /^cc:\/\/image\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.(?:png|jpg|gif|webp))$/i.exec(url.trim());
  return m ? (m[1] as string) : null;
};

export function resolveImage(id: string): Promise<string> {
  let p = resolved.get(id);
  if (!p) {
    p = api.image(id).then((res) => `data:${res.type};base64,${res.data}`);
    // A failed fetch is not remembered, so the next paint tries again.
    p.catch(() => resolved.delete(id));
    resolved.set(id, p);
  }
  return p;
}

/** A File to the base64 the server wants (no data: prefix). */
export function fileToBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}
