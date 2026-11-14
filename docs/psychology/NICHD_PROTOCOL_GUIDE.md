# The NICHD Protocol & Non-Coercive Youth Communication

## 1. Background on the NICHD Investigative Protocol

The **NICHD Protocol** (National Institute of Child Health and Human Development) is the internationally recognized gold standard for interviewing children regarding traumatic events, neglect, and abuse. Developed by Dr. Michael Lamb and colleagues, the protocol eliminates suggestive, coercive, and interrogative questioning, which have been proven to:
1. Inoculate false memories or acquiescence in minors.
2. Compromise the legal admissability of forensic evidence.
3. Compound trauma by inducing shame, guilt, and emotional shutdown.

In Sabi Safe, the NICHD Protocol informs our system prompt constraints and automated guardrail evaluators.

---

## 2. Core Protocol Principles Adapted for Real-Time AI Telephony

### 1. Open-Ended Invitations vs. Focused/Leading Questions

Children have an innate compliance bias when speaking with perceived authority figures (including authoritative-sounding AI voices). If asked leading or forced-choice questions, children frequently guess or agree to appease the speaker.

* **Forbidden (Leading/Suggestive):**
  * *"Did your uncle touch you when you were alone in the room?"*
  * *"Were you scared when that happened?"*
  * *"Was he wearing a blue shirt?"*
* **Mandated (Open Invitations):**
  * *"Tell me what happened next."*
  * *"You can tell me as much or as little as you want."*
  * *"What happened after that?"*

### 2. Elimination of "Why" Questions (Attributional Blame)

In child psychology, questions starting with "Why" are universally perceived as accusatory.
* *"Why did you go to his house?"* $\rightarrow$ Child hears: *"It was your fault for going there."*
* *"Why didn't you shout for help?"* $\rightarrow$ Child hears: *"You should have stopped it."*

Sabi Safe's guardrails actively intercept and forbid "Why" structures directed at the victim's actions.

### 3. Ground Rules: Micro-Agency & Permission to Not Know

Before exploring any sensitive narrative, the protocol establishes explicit psychological permissions:
1. **Permission to Say "I Don't Know":** "If I ask something and you don't know the answer, you can just say 'I don't know'."
2. **Permission to Correct:** "If I misunderstand anything you say, tell me: 'Sabi, that is wrong'."
3. **Pacing Control:** "If you want to stop talking about this and just take a break, tell me at any time."

---

## 3. Preserving Forensic Integrity

When a child discloses abuse to an automated system:
- **Never promise destruction of logs:** System records must be cryptographically hashed and scrubbed of raw PII for privacy, but forensic audit logs must be preserved for authorized child protection services.
- **Never interrogate the abuser's identity aggressively:** Record what the child volunteers spontaneously. Forcing a child to identify a perpetrator over an unencrypted voice line can place them in immediate physical peril if overheard.
- **Immediate escalation to human protective services:** An AI is an emotional triage and grounding bridge, not a replacement for social workers or law enforcement.
