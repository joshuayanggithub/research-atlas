/// <reference types="vite/client" />

// Vite `?url` imports resolve to the asset's final URL string (used for the pdf.js worker).
declare module "*?url" {
  const src: string;
  export default src;
}

interface ImportMetaEnv {
  /** Origin the artifact bundle is served from. Unset in dev, where web/public/data is
   *  served directly; set to the object-store prefix in a deployed build. */
  readonly VITE_DATA_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
