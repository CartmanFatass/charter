// Regenerates tests/fixtures/dotenv_golden.json from the REAL `dotenv` package.
//
// Why this exists: charter must emit a dotenv line that the actual parser reads
// back byte-for-byte. Two silent-corruption bugs shipped here because the tests
// checked a hand-written MODEL of dotenv instead of dotenv itself. This script
// encodes each value, parses it with the real library, and REFUSES to write the
// fixture unless every entry round-trips exactly — so a fixture regenerated from
// a broken encoder cannot be produced.
//
// The `enc()` below must mirror `_dotenv_line` in charter/commands_secrets.py.
//
// Usage (needs Node and the dotenv package):
//   cd tests/fixtures && npm i dotenv@17.4.2 && node generate_golden.cjs
const dotenv=require("dotenv"), fs=require("fs");
function enc(v){
  const hasCR=v.includes("\r"), hasLF=v.includes("\n"), hasSQ=v.includes("'");
  if(!hasCR && !(v.includes("#")&&hasSQ) && !(hasSQ&&hasLF)) return "'"+v+"'";
  if(!hasCR && !v.includes("`")) return "`"+v+"`";
  if(!v.includes('"') && !/\\[nr]/.test(v))
    return '"'+v.replace(/\r/g,"\\r").replace(/\n/g,"\\n")+'"';
  return null;
}
// Curated: realistic shapes + every historically-corrupting case + tier boundaries.
const values = [
  "hunter2","two words","", " ", "  lead", "trail  ",
  'a"b', "a'b", "a\\b", "a$bc", "a#b", "a=b", "${VAR}", "back`tick",
  "pä$$—wörd·日本", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-_123",
  "P@ssw0rd!#$%^&*()_+-=[]{}|;:,.<>?",
  "it's", "it'snb".replace("n","\\n"),            // apostrophe + literal \n  (bug #1 of branch)
  "a#b'", "tricky'value #comment", "pw#1'x",      // # + '                    (bug #1 of final review)
  "a'b\"c`d",                                     // all three quote chars
  "c:\\Users\\svc\\new\\report.txt",
  "postgres://u:p%23w@host:5432/db?sslmode=require",
  '{"user":"a","pw":"b#c"}',
  "line1\nline2", "a\nb\nc", "multi\n\nblank",
  "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234\n-----END RSA PRIVATE KEY-----\n",
  "-----BEGIN KEY-----\r\nMIIB\r\n-----END KEY-----\r\n",
  "apiVersion: v1\nclusters:\n- cluster:\n    server: https://x#y\n  name: 'prod'\n",
  "line1\r\nline2", "\r\n", "\n", "  \n  ",
  "a\\b\nc", "path\\to\nfile", "q'and\nnl", "a'b\"c\nd", "both\"and'q",
  "'", '"', "`", "\\", "''", '""', "end'", "'start", "x'y'z", "tab\there",
  "x\\ny\nz", "a\\rb\nc", "it's\\nb\nreal",   // literal \n/\r sequence + a REAL newline
  "#'`\"x", 
];
const unencodable = ["\"\r", "#\"\r", "a\"b\rc", "\r\"", "x\"\r\ny", "#'`\"", "a#'b`c\"d"];
const golden=[], skipped=[];
for(const v of values){
  const line = enc(v);
  if(line===null){ skipped.push(v); continue; }
  const got = dotenv.parse("K="+line).K;
  if(got!==v){ console.error("REFUSING: encoder does not round-trip", JSON.stringify(v), "->", JSON.stringify(got)); process.exit(1); }
  golden.push({value:v, body:line});
}
for(const v of unencodable){
  if(enc(v)!==null){ console.error("REFUSING: expected unencodable", JSON.stringify(v)); process.exit(1); }
}
fs.writeFileSync("dotenv_golden.json", JSON.stringify({
  _README: "Generated from REAL dotenv 17.4.2. 'body' is the quoted value _dotenv_line must emit after 'NAME='. Every entry was verified to round-trip through dotenv.parse. 'unencodable' values must raise ValueError. Regenerate only by re-running the generator against the real parser.",
  dotenv_version: require("dotenv/package.json").version,
  entries: golden, unencodable
}, null, 1));
console.log(`golden: ${golden.length} verified round-trips, ${unencodable.length} unencodable, ${skipped.length} skipped`);
