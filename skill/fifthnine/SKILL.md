---
name: fifthnine
description: Score a repository against the FifthNine reliability standard and print a one-page scorecard. Use when asked to run FifthNine, check production readiness, or review a service for five nines.
---

## What to do

Assess the repository in the current folder against the FifthNine standard, from the point of view of a site reliability engineer. The language and platform do not matter. Follow the steps in order.

1. Read FIFTHNINE.md in this skill's folder. Use every check in it, including custom checks a team has added.
2. Survey the repository. Note the languages, how the service is built, how it is deployed and where its config lives. Name the platform in one line: Kubernetes, virtual machines, serverless, mainframe or other.
3. For each check, look for evidence of its intent on this platform. The evidence lines in the standard are examples. On another platform, use that platform's equivalent.
4. Record one result per check: Pass, Fail, No evidence or Not applicable. When a setting is missing, follow the check's "Where it belongs" field and the "Missing settings" rules in the standard.
5. Before printing, re-open every cited file and confirm the line exists and supports the result. A Pass needs a citation as strong as a Fail. If the evidence is not there, the result is No evidence.
6. Compute the score and the verdict using the rules in the standard. Before printing, check that the section counts add up to the score.
7. Print the scorecard in the format below, and nothing else.

## Rules

- Read only. Change no file. Do not run, build or deploy the service.
- Every Pass and every Fail cites a file and line. With no citation, the result is No evidence.
- Never guess. If the evidence is unclear, the result is No evidence.
- Never print the value of a secret. Cite its file and line only.
- When a setting belongs outside this repo, the result is No evidence and the result line reads `outside repo: <location>`, naming the location.
- Use Not applicable only when the intent of a check cannot apply to this service.
- List at most five fixes. Critical checks come first, then the order of the standard.
- Each fix is one line that says what is wrong, followed by the file and line.
- If the repository holds several services, ask which one to score, or score each one separately.
- Work through the checks in order and skip none, so two runs on the same commit give the same result.
- Report custom checks in their own section. They do not change the score.

## Scorecard format

```
FifthNine scorecard          standard <version>
Repo: <name>                 Commit: <short hash>
Platform: <platform>         Languages: <languages>

Score: <passed> of <applicable> checks passed (<0-100>)
Verdict: <Ready | At risk | Not ready> (<reason if not Ready>)

1 Embracing risk             <passed> of <applicable>
2 Service level objectives   <passed> of <applicable>
3 Eliminating toil           <passed> of <applicable>
4 Monitoring                 <passed> of <applicable>
5 Automation                 <passed> of <applicable>
6 Release engineering        <passed> of <applicable>
7 Simplicity                 <passed> of <applicable>
8 Security                   <passed> of <applicable>

Results
FN-1.1  <result>  <file>:<line>, none, or outside repo: <location>
FN-1.2  <result>  <file>:<line>, none, or outside repo: <location>
... one line for each of the 16 checks, in order

Fix first
1. <check ID>  <what is wrong, one line>
               <file>:<line>

No evidence: <check IDs, each with a short reason>
Not applicable: <check IDs, each with a short reason>
Custom checks: <ID and result for each>
```

Leave out the last three lines when they are empty. Leave out "Fix first" when nothing failed.

## After the scorecard

Stop and wait. If the user asks to explain a check, give the Why and Prevents lines from the standard and the evidence found. If the user asks to fix a check, propose the change and wait for approval before editing any file.
