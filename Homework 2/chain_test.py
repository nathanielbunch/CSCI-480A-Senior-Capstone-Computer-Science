import numpy as np
from chain_links import Chain
from matplotlib import pyplot as plt

reward_distribution = np.zeros(10)
reward_distribution[0] = 0.001
reward_distribution[9] = 1
chain_env = Chain(10, 5, reward_distribution)

#Initialize table with all zeros
Q = np.zeros([chain_env.get_observation_space(),chain_env.get_action_space()])
# Set learning parameters
lr = .8
y = .95
num_episodes = 2000
#create lists to contain total rewards and steps per episode
jList = []
rList = []
for i in range(num_episodes):
    #Reset environment and get first new observation
    s = chain_env.reset()
    rAll = 0
    d = False
    j = 0
    #The Q-Table learning algorithm
    while j < 99:
        j+=1
        #Choose an action by greedily (with noise) picking from Q table
        a = np.argmax(Q[s,:] + np.random.randn(1,chain_env.get_action_space())*(1./(i+1)))
        #Get new state and reward from environment
        #print("i",i, "a", a, "s", s)#, "s1", s1)
        s1,r,d = chain_env.step(a)
        #Update Q-Table with new knowledge
        Q[s,a] = Q[s,a] + lr*(r + y*np.max(Q[s1,:]) - Q[s,a])
        rAll += r
        s = s1
        if d == True:
            break
    jList.append(j)
    rList.append(rAll)

print("Score over time: " +  str(sum(rList)/num_episodes))
print("Average Steps: " + str(np.mean(jList)))
print("Final Q-Table Values")
print(Q)
plt.plot(rList)
plt.show()
plt.plot(jList)
plt.show()