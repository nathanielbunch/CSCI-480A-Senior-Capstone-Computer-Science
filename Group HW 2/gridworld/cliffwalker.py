from collections import namedtuple
import numpy as np


# helper data structures:
# a state is given by row and column positions designated (y, x)
State = namedtuple('State', ['y', 'x'])

# encapsulates a transition to state and its probability
Transition = namedtuple('Transition', ['state', 'prob', 'reward'])  # transition to state with probability prob


class GridWorld:
    """ Cliffwalker. """

    ACTION_LEFT = 0
    ACTION_RIGHT = 1
    ACTION_UP = 2
    ACTION_DOWN = 3
    ACTIONS = [ACTION_LEFT, ACTION_RIGHT, ACTION_UP, ACTION_DOWN]
    FALL_REWARD = -40
    ACTION_NAMES = {ACTION_LEFT: "Left", ACTION_RIGHT: "Right", ACTION_UP: "Up", ACTION_DOWN: "Down"}

    def __init__(self, height, width, random_action_p=0.1, risky_p_loss=0.15):

        self.height, self.width = height, width
        self.risky_p_loss = risky_p_loss
        self.random_action_p = random_action_p

        # self.risky_goal_states = {State(0, 5)}
        self.risky_goal_states = {}

        self.initial_state = State(self.height - 1, 0)
        self.goal_states = {State(self.height - 1, self.width - 1)}

        self.cliff_states = set()
        if height != 1:
            for x in range(width):
                for y in range(height):
                    s = State(y, x)
                    p_cliff = 0.1 * (y / height)**2 * bool(x > 1 and y > 0 and x < width-2 and y < height-1)
                    if s == self.initial_state or s in self.goal_states:
                        continue

                    if np.random.random() < p_cliff:
                        self.cliff_states.add(s)

    def states(self):
        """ iterator over all possible states """
        for y in range(self.height):
            for x in range(self.width):
                s = State(y, x)
                if s in self.cliff_states:
                    continue
                yield s

    def target_state(self, s, a):
        """ Return the next deterministic state """
        x, y = s.x, s.y
        if a == self.ACTION_LEFT:
            return State(y, max(x - 1, 0))
        if a == self.ACTION_RIGHT:
            return State(y, min(x + 1, self.width - 1))
        if a == self.ACTION_UP:
            return State(max(y - 1, 0), x)
        if a == self.ACTION_DOWN:
            return State(min(y + 1, self.height - 1), x)

    def transitions(self, s):
        """
        returns a list of Transitions from the state s for each action, only non zero probabilities are given
        serves the lists for all actions at once
        """
        if s in self.goal_states:
            return [[Transition(state=s, prob=1.0, reward=0)] for a in self.ACTIONS]

        if s in self.risky_goal_states:
            goal = next(iter(self.goal_states))
            return [[Transition(state=goal, prob=self.risky_p_loss, reward=-50),
                     Transition(state=goal, prob=1-self.risky_p_loss, reward=100)] for a in self.ACTIONS]

        transitions_full = []
        for a in self.ACTIONS:
            transitions_actions = []

            # over all *random* actions
            for a_ in self.ACTIONS:
                s_ = self.target_state(s, a_)
                if s_ in self.cliff_states:
                    r = self.FALL_REWARD
                    # s_ = self.initial_state
                    s_ = next(iter(self.goal_states))
                else:
                    r = -1
                p = 1.0 - self.random_action_p if a_ == a else self.random_action_p / 3
                if p != 0:
                    transitions_actions.append(Transition(s_, p, r))
            transitions_full.append(transitions_actions)

        return transitions_full

    def sample_transition(self, s, a):
        """ Sample a single transition, duh. """
        trans = self.transitions(s)[a]
        state_probs = [tran.prob for tran in trans]
        return trans[np.random.choice(len(trans), p=state_probs)]



if __name__ == '__main__':
    from cvar.gridworld.core.constants import *
    from cvar.gridworld.plots.grid import grid_plot
    import matplotlib.pyplot as plt

    world = GridWorld(40, 60)
    grid_plot(world)
    plt.show()
    for i in range(20):
        print('seed=', i)
        np.random.seed(i)
        world = GridWorld(40, 60)
        grid_plot(world)
        plt.show()