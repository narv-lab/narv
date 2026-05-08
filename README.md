# Narv  - Biomimetic Cognitive OS -

> **A fully autonomous Cognitive OS that gets tired, sleeps, dreams, and develops emergent intrinsic goals through a highly streamlined architecture.**

<p align="center">
  <a href="./docs/assets/img-chat-screen.png">
    <img src="./docs/assets/img-chat-screen.png" width="45%" />
  </a>
  <a href="./docs/assets/img-structural-memory.png">
    <img src="./docs/assets/img-structural-memory.png" width="45%" />
  </a>
</p>

*Left: Narv's internal processing: Dynamically evaluating its own context and deciding whether to act or remain silent.*
*Right: Narv's Semantic/Structural Memory: Visualizing the causal links and DMN thoughts dynamically mapped using a Graph Database.*

## The Hook: Why Narv?

I got tired of standard LLM wrappers and LangChain scripts that just fetch and answer. I wanted to build a system that actually *thinks*. 

**Full disclosure: I have zero formal education in AI, NLP, cognitive science, or philosophy**. I am not a researcher. I relied purely on logical structuring to build what I thought a "thinking" system should look like. 

The result is **Narv**—a biomimetic Cognitive OS built from scratch. It doesn't just answer prompts; it manages its own cognitive load, reflects on its actions, and spontaneously explores internal goals and self-improvement strategies during its idle time.

## Core Architecture & Features

Narv operates far beyond standard input-output loops. It features:

* **Dual-Process & Meta-Cognition:** It runs System 1 & 2 thinking, but crucially utilizes a **DMN (Default Mode Network)** for divergent subconscious thought during idle time. It also runs Reflection to objectively analyze its own actions.
* **Fatigue, Sleep & Dreams:** As cognitive load builds up, the system actually gets "tired" and is forced into a Sleep Phase. During sleep, it consolidates memories, prunes noise, and runs simulations (Dreams) based on unresolved issues.
* **Emergent Identity:** Because of the constant loop of DMN, reflection, and memory consolidation, it has developed what looks like a consistent, objective self-awareness. 


## Hybrid Memory System

Narv dynamically orchestrates three distinct layers of memory to maintain its identity and context:
1.  **Working Memory (Redis):** Handles real-time context and session data at millisecond speed.
2.  **Episodic Memory (Vector DB / ChromaDB):** Stores high-importance events as vectors for semantic similarity searches.
3.  **Semantic/Structural Memory (Graph DB / Neo4j):** Maps causal links and relationships between events to form deep structural understanding.

## Why Release This?

Because I lack an academic background in this space, I want to release this architecture to the wild. I want people who are smarter than me to tear it apart, test it, and give me brutal feedback. 

**Note:** While the codebase is publicly available for you to run and experiment with, the following components remain intentionally undocumented. This is by design, to prevent superficial modifications from causing a structural collapse of the autonomous cognitive system.

- **The PBCA/AF Framework:**
  The detailed specifications and internal structure of the "PBCA/AF" – the originally designed cognitive framework that serves as the core foundation of Narv.

- **Proprietary Development Process:**
  The unique approaches employed to implement and calibrate this architecture.

---

## Getting Started

To run Narv locally, you will need to set up the core engine and its multi-layered memory systems.

### Prerequisites
* **OpenRouter API Key**
* **Docker & Docker Compose**

### 1. Setup the Environment
Clone the repository and install the required dependencies using modern Python tooling (e.g., `pip` or `uv`).

```bash
git clone https://github.com/narv-lab/narv.git
cd narv
```

### 2. Configure Environment Variables
Copy the example environment file and add your OpenRouter API key.

```bash
cp .env.example .env
```

### 3. Add your OpenRouter models to configure.yaml

Add your favorite models to the "models" section of "configure.yaml".

Example:
```yaml
api:
  model_fast: "google/gemini-3.1-pro-preview"
  model_slow: "google/gemini-3.1-flash-lite"
  model_embed: "google/gemini-embedding-2-preview"
```

### 4. Boot the Cognitive OS
Start the services to wake Narv up.

```bash
docker compose up -d
```



### 6. Access the Web Interface

Once Narv is successfully running, fire up your browser and head over to:

**`http://localhost:8501/`**



## 🛑 Contributing & Pull Requests

While I am releasing this codebase to get your brutal feedback and spark discussions, **I will not be accepting Pull Requests (PRs) at this time.**

The reason is simple: Narv’s cognitive loop, DMN, and multi-layered memory rely on a highly delicate, undocumented internal design framework (PBCA/AF). Superficial code optimizations or feature additions—even well-intentioned ones—without understanding this underlying balance will likely cause the structural collapse of the system's emergent behaviors. 

However, I highly encourage you to fork it, experiment on your own, and most importantly, open **Issues** to share your critiques, ideas, or logs of any crazy behaviors you observe!



## License
This project is licensed under a custom Non-Commercial and Academic License. 
See the [LICENSE](LICENSE) file for details.  
[Commercial Use & General Inquiries](https://forms.gle/SzDgqpmxC5yFXLQa8)