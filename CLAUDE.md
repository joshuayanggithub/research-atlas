This tool is a research visualizer web application. 

### Organization
It allows one to view the research of any organization in any granularity. This includes 
    - Universities (Berkeley), Departments (CMU's MLD or RI) and individual Research Labs/Groups within (Berkley's BAIR, CMU's BIG lab, Biorobotics lab) 
    - Companies (Amazon, Google) and individual research Groups (FAIR, Meta Superintelligence, Amazon FAR, Amazon AGI, Google Deepmind, Google Brain, etc)
    - NeoLabs (Redwood Research, Anthropic, OpenAI, Deepseek, Kimi, Minimax)

The tool allows us to filter computer science research works, particularly AI/ML works, by granularity. 

What counts as research are publications, which loosely can be defined as those published on ArXiv (which may not be affliliated with conferences), and especially those submitted and accepted to conferences. 

### Topics
The visualizer uses a feasible embedding model to very detailedly embed at least the abstract and title (and more depth if needed) into vectors that can be used to visualize the closness of related research works. The view is in graph view, where nodes (research works) close to other nodes are very similar research works. Directed edges should include citations. Think about what size and how to do this step carefully. 

With this graph view and embedding, zooming in and out in this tool allows grouping different sizes of clusters of nodes the topics of related works by granularity:
    - zooming in you should be able to see similar subtopics such as action-conditioned world models vs World Action Models
    - in the middle you should see topics in machine learning such as self supervised learning, world models, etc
    - zooming out could mean viewing computer architecture, vs computer systems work
This is a very important feature and think carefully about how to implement this with embedding model. 

### Researchers / Time Periods
You must also be able to filter works by author and date (start date - end date) in graph view. 

### Related Works

Some ideas for organization are https://github.com/emeryberger/CSRankings. Clone this and analyze how it works for generating

Anothe idea is clustering related works by citations, we know that citations between works intrinsically means the works are related, even though no explicit notion of topic is defined by in/out edges. This is seen from https://www.connectedpapers.com/

### Logging
Outline important design decisions in a Design.md file and outline key features of the app in a Features.md file. 