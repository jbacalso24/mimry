# Supported languages

MIMRY parses these languages with tree-sitter.
Programming languages give symbols, imports, calls and inheritance; markup, style and data formats give the names an agent searches them by, described below the table.

| Language | Extensions |
|---|---|
| Python | `.py` |
| JavaScript | `.cjs`, `.js`, `.jsx`, `.mjs` |
| TypeScript | `.cts`, `.mts`, `.ts`, `.tsx` |
| Go | `.go` |
| Rust | `.rs` |
| C# | `.cs` |
| Java | `.java` |
| PHP | `.php` |
| C | `.c`, `.h` |
| C++ | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` |
| Ruby | `.rb` |
| Kotlin | `.kt`, `.kts` |
| Scala | `.sc`, `.scala` |
| Groovy | `.gradle`, `.groovy`, `.gvy` |
| Swift | `.swift` |
| Bash | `.bash`, `.sh` |
| PowerShell | `.ps1`, `.psm1` |
| Lua | `.lua` |
| Perl | `.pl`, `.pm` |
| Dart | `.dart` |
| Objective-C | `.m` |
| Zig | `.zig` |
| Elixir | `.ex`, `.exs` |
| Haskell | `.hs` |
| OCaml | `.ml`, `.mli` |
| R | `.r` |
| Julia | `.jl` |
| Solidity | `.sol` |
| Fortran | `.f03`, `.f08`, `.f90`, `.f95` |
| Pascal | `.dpr`, `.lpr`, `.pas`, `.pp` |
| CSS | `.css` |
| SCSS | `.scss` |
| HTML | `.htm`, `.html` |
| Vue | `.vue` |
| Svelte | `.svelte` |
| SQL | `.sql` |
| JSON | `.json`, `.jsonc` |
| YAML | `.yaml`, `.yml` |
| TOML | `.toml` |
| XML | `.csproj`, `.fsproj`, `.nuspec`, `.plist`, `.props`, `.resx`, `.targets`, `.vbproj`, `.xaml`, `.xml`, `.xsd` |
| Terraform (HCL) | `.hcl`, `.tf`, `.tfvars` |

Fortran is read in free form only. Fixed-form `.f` and `.for` files do not parse with the grammar MIMRY uses, so they get no symbols or edges.

`.pp` and `.m` are checked first, because most `.pp` files are Puppet and most `.m` files are MATLAB.
A `.pp` file is Pascal only when it has a `unit`, `program` or `library` line, and a `.m` file is Objective-C only when it has an `@interface`, `@implementation`, `@protocol` or `#import` line.
Any other `.pp` or `.m` file is indexed as searchable text with no symbols.

The markup and style languages get what an agent searches them by.
CSS and SCSS give their class and id selectors, custom properties, keyframes, Sass mixins, functions, placeholders and variables, with `@import`, `@use` and `@forward` as imports.
HTML gives each element id and the local stylesheets and scripts it loads.
Vue and Svelte give each component (named after its file) and run the JavaScript or TypeScript and CSS or SCSS passes over their `<script>` and `<style>` blocks, so their line numbers match the file.

MIMRY also indexes Markdown (headings, links, frontmatter), plain text, PDF, Word, Excel and PowerPoint files. Results name the page, section, sheet or slide that matches.

Some files get more structure than their text:

- SQL: `.sql` files give their schema objects and the tables they touch, see below. Python string literals keep their `CREATE TABLE` names, and Alembic migrations keep their revision lineage.
- Groovy and Gradle: `.groovy`, `.gradle` and `.gvy` files give their classes, interfaces, traits, enums, methods and functions, and the `task` entries of a Gradle build, see below.
- Manifests: `package.json`, `pyproject.toml`, `tsconfig.json`, `vite.config.*`, `next.config.*`, `app.json`, `app.config.*`, `.env.example`, `CLAUDE.md`, `AGENTS.md` and `README.md` give their commands, frameworks, entry points and env variable names, never values.

The data formats get the names an agent looks them up by.
JSON, YAML and TOML give their keys to depth two, named by path, such as `services.web` or `tool.ruff.line-length`, and TOML also gives each table header.
A YAML document that is a Kubernetes object also gives a `<kind>/<name>` resource, such as `Deployment/web`.
XML gives each element with an `id` or `name` attribute, and a Maven `pom.xml` gives its `groupId:artifactId`.
MSBuild project and props files give each property in a `PropertyGroup` and each `Target` name, with each `ProjectReference` as an import, a plist gives the keys of its top-level dict, and XAML gives each `x:Name` and `x:Key`.
Terraform gives each `resource`, `data`, `module`, `variable`, `output`, `provider` and `locals` entry under the name it is referenced by, such as `aws_s3_bucket.b`, `data.aws_iam_policy_document.p`, `module.bucket`, `var.region` and `local.name`.
A `var.region` reference finds the variable in the same folder, and a module's local `source` links to its folder.
Values are never symbols, so a secret stored as a value is never indexed.
Lock files and files over 256 KiB get no symbols, and manifests keep their own adapter above.
Every data file is also indexed as text, so `mimry find` matches its content.

SQL files name each schema object as the source writes it, such as `public.users`: tables, views, materialized views, indexes, sequences, schemas and types, and functions, procedures and triggers.
A view, function, trigger or index that reads or writes a table gets a call edge to it, and so does a foreign key's `REFERENCES` target.
A plain query or an `ALTER TABLE` belongs to no object, so it adds no edge.
A statement the parser does not know costs only itself, and the objects around it are kept.
A trigger written with a `BEGIN ... END` body gives its name and table, but its body is not read.
Files over 256 KiB get no symbols, so a large dump stays text-searchable.

Groovy and Gradle files name each class, interface, trait, enum, method and top-level `def` function as the source writes it.
A Spock feature method keeps its string, such as `adds two numbers`, as its name.
A Gradle `task name` or `tasks.register("name")` gives a `task`, and `plugins {}` and `dependencies {}` entries are not symbols.
An `import` of a Groovy class links exactly to the file that declares it, and a wildcard import such as `a.b.*` links to every file of package `a.b`.
A static wildcard such as `import static a.b.C.*` links to the file of class `C`.
Calls inside a method body, and the `extends` and `implements` clauses of a type, give call and inheritance edges, and a paren-less `name arg` call counts only for a method of the same file.
A piece of a file the parser cannot read costs only itself, so an annotated type such as `@Service(Book)` keeps its methods.
Closure-assigned fields and operator methods such as `<<` are not symbols.

Other extensions can be mapped to a supported language in `mimry.toml`, see [Configuration](configuration.md).
