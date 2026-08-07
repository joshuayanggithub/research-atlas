/// <reference types="vite/client" />

// Vite `?url` imports resolve to the asset's final URL string (used for the pdf.js worker).
declare module "*?url" {
  const src: string;
  export default src;
}
