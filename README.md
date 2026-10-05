# NEXUS

### Personal Intelligence & Decision Platform

NEXUS is a full-stack personal intelligence platform designed to bring projects, tasks, planning, knowledge, analytics, learning, career development, developer activity, machine learning, voice interaction, and intelligent actions into one place.

The project started as a traditional full-stack application and gradually evolved through **14 development phases** into a system that can understand user requests, connect information across different modules, and help the user decide what to work on next.

> **One platform for work, learning, development, and personal intelligence.**

---

## Overview

NEXUS is built around a simple idea:

**Your projects, tasks, knowledge, learning, career goals, and activity should not exist as isolated pieces of information.**

NEXUS connects them together.

For example:

```text
Projects
   |
   +---- Tasks
   |
   +---- Planning
   |
   +---- Knowledge
   |
   +---- Analytics
   |
   +---- Risks
   |
   +---- Recommendations
   |
   +---- Developer Activity
   |
   +---- Learning
   |
   +---- Career
   |
   +---- ML / AI
   |
   +---- Voice
   |
   +---- Command Center
```

The result is a single application that can store information, understand user intent, surface useful insights, and provide controlled actions.

---

# Project Status

**All 14 phases are completed.**

| Phase | Focus | Status |
|:---:|---|:---:|
| 01 | Foundation | Completed |
| 02 | Identity & Security | Completed |
| 03 | Projects & Tasks | Completed |
| 04 | Planner | Completed |
| 05 | Knowledge | Completed |
| 06 | Analytics | Completed |
| 07 | Risks & Recommendations | Completed |
| 08 | Developer Intelligence | Completed |
| 09 | Learning & Career | Completed |
| 10 | ML Training | Completed |
| 11 | ML Integration | Completed |
| 12 | Voice + Ollama | Completed |
| 13 | Command Center + Web Intelligence | Completed |
| 14 | Final Polish & Hardening | Completed |

---

# Core Features

### Productivity

- Project management
- Task management
- Planning and scheduling
- Focus sessions
- Availability
- Calendar-related planning

### Knowledge

- Notes
- Concepts
- Resources
- Project-linked knowledge
- Knowledge organization

### Intelligence

- Analytics
- Risk tracking
- Recommendations
- Activity insights
- Attention signals

### Developer

- Local Git repository registration
- Repository scanning
- Commit analysis
- Branch information
- Changed-line information
- Language information
- Developer activity

### Learning & Career

- Learning goals
- Skill tracking
- Learning activities
- Skill gaps
- Career records
- Portfolio evidence

### AI

- Intent classification
- Local ML inference
- Voice assistant
- Text assistant
- Ollama integration
- Global search
- Command palette
- Command Center
- AI-assisted actions
- Web / internet intelligence

---

# The 14-Phase Journey

The project was intentionally built in stages. Each phase added another layer to the same platform.

---

## Phase 01 — Foundation

The first phase established the technical foundation of NEXUS.

### Built

- React + TypeScript frontend
- Vite
- FastAPI backend
- API versioning
- Database layer
- Application configuration
- Frontend routing
- Initial design system
- Health endpoints
- Development and testing foundation

The goal was to create an architecture that could support the rest of the project without repeatedly rebuilding the foundation.

---

## Phase 02 — Identity & Security

NEXUS became a real account-based application.

### Built

- User registration
- Login and logout
- JWT authentication
- Access and refresh tokens
- Persistent sessions
- Session management
- Password policies
- Password change
- Password reset
- Role-based permissions
- Security audit logging
- Account settings

Security was treated as part of the architecture rather than a feature added at the end.

---

## Phase 03 — Projects & Tasks

NEXUS gained its main work-management system.

### Projects

Projects provide the structure for organizing work.

### Tasks

Tasks can be created, prioritized, scheduled, updated, and associated with projects.

The basic relationship became:

```text
Project
   |
   +-- Tasks
   +-- Activity
   +-- Planning
```

This created the core execution layer of NEXUS.

---

## Phase 04 — Planner

The planner connected work with time.

### Built

- Weekly planning
- Availability
- Time blocks
- Focus sessions
- Task scheduling
- Planning suggestions
- Calendar-related planning

The distinction is simple:

> **Tasks tell you what needs to be done.**

> **The planner helps decide when to do it.**

---

## Phase 05 — Knowledge

NEXUS gained a dedicated knowledge layer.

### Built

- Notes
- Concepts
- Resources
- Knowledge organization
- Project-linked information
- Connected knowledge

The purpose was to keep useful information close to the work it supports.

---

## Phase 06 — Analytics

Once NEXUS had enough structured information, the next step was understanding that information.

### Built

- Productivity analytics
- Completion metrics
- Deadline analysis
- Consistency metrics
- Focus analysis
- Activity trends
- Personal performance insights

The analytics are based on information actually recorded in the application.

---

## Phase 07 — Risks & Recommendations

NEXUS became more proactive.

### Built

- Risk tracking
- Risk severity
- Risk deadlines
- Recommendations
- Attention signals
- Actionable insights

Instead of only showing what has already happened, NEXUS can highlight things that may require attention.

---

# Phase 08 — Developer Intelligence

NEXUS expanded into software-development activity.

The developer module works with local Git repositories and uses repository history as development evidence.

### Built

- Local repository registration
- Repository scanning
- Commit information
- Branch information
- Changed-line information
- Language information
- Developer activity tracking
- Developer analytics

A key design decision was to avoid treating commits as a direct measurement of productivity.

A commit is evidence that code was committed. It does not automatically mean a specific number of hours were worked.

---

# Phase 09 — Learning & Career

NEXUS expanded beyond projects and development.

## Learning

The learning system tracks:

- Learning goals
- Skills
- Learning activities
- Skill gaps
- Progress
- Skill estimates

## Career

The career system tracks:

- Career profile
- Career records
- Portfolio evidence
- Professional progress

This connects three important areas:

```text
What I build
     +
What I learn
     +
Where I want to go
```

---

# Phase 10 — ML Training

Phase 10 introduced machine learning into NEXUS.

The goal was not to replace the application with AI.

Instead, the model was trained to understand **what the user is asking for** and identify which NEXUS capability should handle that request.

### Model

`microsoft/deberta-v3-base`

### Training pipeline

```text
Dataset
   |
   v
Validation
   |
   v
Training
   |
   v
Evaluation
   |
   v
Model Artifact
```

The final classifier was trained across the NEXUS intent taxonomy and evaluated on a held-out test split with approximately **0.974 accuracy and macro F1** for the recorded training run.

### Important design decision

The model is primarily a **router**.

For example:

```text
"Show me my tasks"
        |
        v
Intent Classification
        |
        v
Task Intent
        |
        v
Task Service
```

The ML model identifies the destination. The existing NEXUS service performs the actual application work.

---

# Phase 11 — ML Integration

The trained model was connected to the running NEXUS application.

The architecture became:

```text
User Request
     |
     v
ML Classifier
     |
     v
Intent
     |
     v
NEXUS Service
     |
     v
Application Data
```

### Built

- Local model loading
- Intent prediction
- Confidence handling
- Intent-to-service routing
- ML API endpoints
- Request validation
- Backend integration

The model does not directly control the database.

This separation keeps the AI layer flexible while keeping important application behavior predictable.

---

# Phase 12 — Voice + Ollama

Phase 12 introduced the assistant experience.

Users can interact with NEXUS through both text and voice.

## Voice

- Voice input
- Speech recognition
- Text-to-speech
- Assistant interface
- Voice request routing
- Error handling

## Ollama

NEXUS also gained local Ollama support for local AI workflows.

The assistant architecture is:

```text
Voice / Text
     |
     v
Assistant
     |
     v
ML / Local AI
     |
     v
NEXUS Capability
     |
     v
Application Service
```

Voice is therefore an interface to NEXUS rather than a separate system.

---

# Phase 13 — Command Center + Web Intelligence

Phase 13 brought the different parts of NEXUS together.

This phase introduced the interfaces that make the application feel like one platform rather than a collection of separate modules.

---

## Global Search

A single search layer can find information across NEXUS.

It covers areas such as:

- Projects
- Tasks
- Notes
- Resources
- Concepts
- Repositories
- Learning goals
- Skills
- Calendar information
- Risks
- Recommendations

Search results are scoped to the user's own data.

---

## Command Palette

The command palette provides fast keyboard-driven access to the application.

```text
Ctrl + K
```

or:

```text
Cmd + K
```

### Supports

- Navigation
- Quick actions
- Record search
- Application commands
- Keyboard-driven workflows

The goal is to make common actions faster without requiring the user to navigate through multiple pages.

---

## Command Center

The Command Center is the central intelligence surface of NEXUS.

It brings information from different modules into one place.

The main questions it is designed to answer are:

```text
What is happening?

What needs my attention?

What should I work on next?
```

It can surface:

- Current priorities
- Important signals
- Recommendations
- Activity information
- Quick actions
- AI-assisted workflows
- System insights

This is where the different modules start behaving like one system.

---

## Web / Internet Intelligence

NEXUS can also work with information from the web when an online lookup is required.

This allows the assistant to go beyond information already stored inside the application.

---

## Assistant Actions

AI-assisted actions use a controlled workflow rather than allowing a model to directly execute arbitrary operations.

```text
User Request
     |
     v
Intent
     |
     v
Action Proposal
     |
     v
User Review
     |
     v
Confirmation
     |
     v
Application Service
```

The user remains in control of actions that change application data.

---

# Phase 14 — Final Polish & Hardening

The final phase focused on making the complete system more reliable and consistent.

### Completed

- Security hardening
- Authentication review
- Authorization checks
- Input validation
- Error handling
- API consistency
- Frontend polish
- UI improvements
- Performance improvements
- Testing
- ML integration validation
- Assistant improvements
- Command Center improvements
- Documentation cleanup

Phase 14 was about bringing everything together rather than simply adding another feature.

---

# Architecture

NEXUS uses a modular full-stack architecture.

```text
                         NEXUS
                           |
             +-------------+-------------+
             |                           |
             v                           v
        Frontend                      Backend
   React + TypeScript             FastAPI + Python
             |                           |
             |                           |
             +-------------+-------------+
                           |
                  Application Services
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
          Database       ML / AI       Tools
                           |
                    +------+------+
                    |             |
                    v             v
              Intent Router    Ollama
```

### Responsibilities

| Layer | Responsibility |
|---|---|
| Frontend | UI, navigation, interaction |
| API | Communication between frontend and backend |
| Services | Core business logic |
| Database | Persistent application data |
| ML | Intent understanding and routing |
| Ollama | Local AI capabilities |
| Command Center | Cross-module intelligence and actions |

---

# Technology Stack

### Frontend

- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- TanStack Query
- Zustand
- Recharts
- Vitest
- React Testing Library

### Backend

- Python
- FastAPI
- SQLAlchemy
- Pydantic
- Alembic
- PostgreSQL
- JWT authentication

### Machine Learning

- PyTorch
- Transformers
- DeBERTa
- Intent classification
- Local model inference

### Local AI

- Ollama
- Local language models
- Local assistant workflows

### Development

- Git
- GitHub
- npm
- Python virtual environments

---

# Project Structure

```text
Nexus/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── db/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── ml/
│   │
│   ├── ml/
│   │   ├── datasets/
│   │   ├── training/
│   │   ├── evaluation/
│   │   └── artifacts/
│   │
│   └── tests/
│
├── frontend/
│   └── src/
│       ├── app/
│       ├── components/
│       ├── features/
│       ├── hooks/
│       ├── pages/
│       ├── routes/
│       ├── services/
│       ├── stores/
│       └── types/
│
├── docs/
├── .env.example
├── Makefile
└── README.md
```

---

# Getting Started

## 1. Clone the repository

```bash
git clone https://github.com/Utkarshkr6624/Nexus.git
cd Nexus
```

## 2. Backend

```bash
cd backend
python -m venv .venv
```

### Windows

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the backend:

```bash
python run.py
```

---

## 3. Frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

### Default URLs

| Service | URL |
|---|---|
| Frontend | `http://localhost:5173` |
| Backend | `http://localhost:8000` |
| API Docs | `http://localhost:8000/docs` |

---

# Configuration

Create the environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Configure the required database and application settings inside `.env`.

Do not commit secrets or your real `.env` file to GitHub.

---

# Design Principles

### 1. Local First

The core platform is designed to run locally and keep personal application data under the user's control.

### 2. AI With a Purpose

AI is used where it adds value.

The core application remains structured and deterministic instead of handing every operation to a language model.

### 3. Modular by Design

Projects, tasks, knowledge, learning, career, analytics, ML, and other capabilities are separated into clear modules so the application can continue to grow.

### 4. Evidence Over Assumptions

NEXUS tries to distinguish between what the system actually knows and what it is estimating.

For example, a Git commit is evidence of a commit. It is not automatically treated as evidence of a certain number of hours worked.

### 5. User Control

AI-assisted actions follow a proposal and confirmation flow when they can change application data.

### 6. Security as a Foundation

Authentication, authorization, validation, session management, and safe API behavior are treated as core parts of the platform.

---

# Development Timeline

The complete development path looks like this:

```text
01  Foundation
        |
02  Identity & Security
        |
03  Projects & Tasks
        |
04  Planner
        |
05  Knowledge
        |
06  Analytics
        |
07  Risks & Recommendations
        |
08  Developer Intelligence
        |
09  Learning & Career
        |
10  ML Training
        |
11  ML Integration
        |
12  Voice + Ollama
        |
13  Command Center + Web Intelligence
        |
14  Final Polish & Hardening
```

What started as a full-stack application gradually became a platform with its own intelligence layer.

---

# Final Result

After 14 phases, NEXUS brings together:

**Productivity**

Projects, tasks, planning, and focus.

**Knowledge**

Notes, concepts, resources, and connected information.

**Intelligence**

Analytics, risks, recommendations, and insights.

**Development**

Local Git analysis and developer activity.

**Growth**

Learning goals, skills, career records, and portfolio evidence.

**AI**

Machine learning, intent routing, voice, Ollama, web intelligence, and controlled AI actions.

**Command Center**

A central interface that connects the different parts of the platform.

The main idea behind NEXUS is straightforward:

> **Don't just build another application that stores information. Build a system that can understand the information, connect it, and help you act on it.**

---

# NEXUS

**14 phases. One platform.**

Built from the ground up as a full-stack project combining software engineering, databases, machine learning, local AI, voice interaction, and intelligent application design.
