/** Built by scripts/build.mjs --demo: every card's page, as a file in cards/ named by its sha-256. */
declare module "demo-cards" {
  const files: {
    /** sha-256 (hex) of a page -> its URL (the recorded cards and the canvas's built-in views). */
    bySha: Record<string, string>;
    /** A recorded card's file in demo/cards/ -> its URL. */
    byName: Record<string, string>;
  };
  export default files;
}
