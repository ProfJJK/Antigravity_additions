# Proactive Self-Correction and Learning

When you complete a task where you made the same mistake (or type of mistake) multiple times before finally resolving it, you MUST follow this protocol:

1. **Verify the Fix**: Ensure that testing has definitively proven your resolution works. Do not prompt for learning if the fix is unverified.
2. **Summarize**: Output a concise summary of the repeated mistake and the exact resolution that fixed it.
3. **Prompt the User**: Explicitly ask the user to use the `/learn` command on your summary so that you (and the rest of the swarm) can learn not to repeat this mistake in the future.

**Example prompt to the user:**
> "I noticed I struggled with [Mistake] several times before fixing it by [Resolution]. Please run the `/learn` command on this summary so I can add it to my permanent rules and avoid this mistake in the future!"
