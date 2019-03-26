import numpy as np
import os
import dill
import tempfile
import tensorflow as tf
import zipfile

import baselines.common.tf_util as U

from baselines import logger
from baselines.common.schedules import LinearSchedule
from .replay_buffer import ReplayBuffer, PrioritizedReplayBuffer
from .build_graph import build_act, build_train
from cvar.common.util import timed
from .static import make_session


class ActWrapper(object):
    def __init__(self, act, act_params):
        self._act = act
        self._act_params = act_params

    @staticmethod
    def load(path, num_cpu=4):
        with open(path, "rb") as f:
            model_data, act_params = dill.load(f)
        act = build_act(**act_params)
        sess = make_session(num_cpu=num_cpu)
        sess.__enter__()
        with tempfile.TemporaryDirectory() as td:
            arc_path = os.path.join(td, "packed.zip")
            with open(arc_path, "wb") as f:
                f.write(model_data)

            zipfile.ZipFile(arc_path, 'r', zipfile.ZIP_DEFLATED).extractall(td)
            U.load_state(os.path.join(td, "model"))

        return ActWrapper(act, act_params)

    @staticmethod
    def reload(path):
        with open(path, "rb") as f:
            model_data, act_params = dill.load(f)

        with tempfile.TemporaryDirectory() as td:
            arc_path = os.path.join(td, "packed.zip")
            with open(arc_path, "wb") as f:
                f.write(model_data)

            zipfile.ZipFile(arc_path, 'r', zipfile.ZIP_DEFLATED).extractall(td)
            U.load_state(os.path.join(td, "model"))

    def __call__(self, *args, **kwargs):
        return self._act(*args, **kwargs)

    def save(self, path):
        """Save model to a pickle located at `path`"""
        with tempfile.TemporaryDirectory() as td:
            U.save_state(os.path.join(td, "model"))
            arc_name = os.path.join(td, "packed.zip")
            with zipfile.ZipFile(arc_name, 'w') as zipf:
                for root, dirs, files in os.walk(td):
                    for fname in files:
                        file_path = os.path.join(root, fname)
                        if file_path != arc_name:
                            zipf.write(file_path, os.path.relpath(file_path, td))
            with open(arc_name, "rb") as f:
                model_data = f.read()
        with open(path, "wb") as f:
            dill.dump((model_data, self._act_params), f)

    def get_nb_atoms(self):
        return self._act_params['nb_atoms']


def load(path, num_cpu=16):
    """Load act function that was returned by learn function.

    Parameters
    ----------
    path: str
        path to the act function pickle
    num_cpu: int
        number of cpus to use for executing the policy

    Returns
    -------
    act: ActWrapper
        function that takes a batch of observations
        and returns actions.
    """
    return ActWrapper.load(path, num_cpu=num_cpu)


@timed
def learn(env,
          var_func,
          cvar_func,
          nb_atoms,
          run_alpha=None,
          lr=5e-4,
          max_timesteps=100000,
          buffer_size=50000,
          exploration_fraction=0.1,
          exploration_final_eps=0.01,
          train_freq=1,
          batch_size=32,
          print_freq=1,
          checkpoint_freq=10000,
          learning_starts=1000,
          gamma=0.95,
          target_network_update_freq=500,
          num_cpu=4,
          callback=None,
          periodic_save_freq=1000000,
          periodic_save_path=None,
          grad_norm_clip=None,
          ):
    """Train a CVaR DQN model.

    Parameters
    -------
    env: gym.Env
        environment to train on
    var_func: (tf.Variable, int, str, bool) -> tf.Variable
        the model that takes the following inputs:
            observation_in: object
                the output of observation placeholder
            num_actions: int
                number of actions
            scope: str
            reuse: bool
                should be passed to outer variable scope
        and returns a tensor of shape (batch_size, num_actions) with values of every action.
    cvar_func: function
        same as var_func
    nb_atoms: int
        number of atoms used in CVaR discretization
    run_alpha: float
        optimize CVaR_alpha while running. None if you want random alpha each episode.
    lr: float
        learning rate for adam optimizer
    max_timesteps: int
        number of env steps to optimizer for
    buffer_size: int
        size of the replay buffer
    exploration_fraction: float
        fraction of entire training period over which the exploration rate is annealed
    exploration_final_eps: float
        final value of random action probability
    train_freq: int
        update the model every `train_freq` steps.
        set to None to disable printing
    batch_size: int
        size of a batched sampled from replay buffer for training
    print_freq: int
        how often to print out training progress
        set to None to disable printing
    checkpoint_freq: int
        how often to save the best model. This is so that the best version is restored
        at the end of the training. If you do not wish to restore the best version at
        the end of the training set this variable to None.
    learning_starts: int
        how many steps of the model to collect transitions for before learning starts
    gamma: float
        discount factor
    target_network_update_freq: int
        update the target network every `target_network_update_freq` steps.
    num_cpu: int
        number of cpus to use for training
    callback: (locals, globals) -> None
        function called at every steps with state of the algorithm.
        If callback returns true training stops.
    periodic_save_freq: int
        How often do we save the model - periodically
    periodic_save_path: str
        Where do we save the model - periodically
    grad_norm_clip: float
        Clip gradient to this value. No clipping if None
    Returns
    -------
    act: ActWrapper
        Wrapper over act function. Adds ability to save it and load it.
        See header of baselines/distdeepq/categorical.py for details on the act function.
    """
    # Create all the functions necessary to train the model

    sess = make_session(num_cpu=num_cpu)
    sess.__enter__()

    obs_space_shape = env.observation_space.shape

    def make_obs_ph(name):
        return U.BatchInput(obs_space_shape, name=name)

    act, train, update_target, debug = build_train(
        make_obs_ph=make_obs_ph,
        var_func=var_func,
        cvar_func=cvar_func,
        num_actions=env.action_space.n,
        optimizer=tf.train.AdamOptimizer(learning_rate=lr),
        gamma=gamma,
        nb_atoms=nb_atoms,
        grad_norm_clipping=grad_norm_clip
    )

    act_params = {
        'make_obs_ph': make_obs_ph,
        'cvar_func': cvar_func,
        'var_func': var_func,
        'num_actions': env.action_space.n,
        'nb_atoms': nb_atoms
    }

    # Create the replay buffer
    replay_buffer = ReplayBuffer(buffer_size)
    beta_schedule = None
    # Create the schedule for exploration starting from 1.
    exploration = LinearSchedule(schedule_timesteps=int(exploration_fraction * max_timesteps),
                                 initial_p=1.0,
                                 final_p=exploration_final_eps)

    # Initialize the parameters and copy them to the target network.
    U.initialize()
    update_target()

    episode_rewards = [0.0]
    saved_mean_reward = None
    obs = env.reset()
    reset = True
    episode = 0
    alpha = 1.

    # --------------------------------- RUN ---------------------------------
    with tempfile.TemporaryDirectory() as td:
        model_saved = False
        model_file = os.path.join(td, "model")
        for t in range(max_timesteps):
            if callback is not None:
                if callback(locals(), globals()):
                    print('Target reached')
                    model_saved = False
                    break
            # Take action and update exploration to the newest value
            update_eps = exploration.value(t)

            update_param_noise_threshold = 0.

            action = act(np.array(obs)[None], alpha, update_eps=update_eps)[0]
            reset = False
            new_obs, rew, done, _ = env.step(action)

            # ===== DEBUG =====

            # s = np.ones_like(np.array(obs)[None])
            # a = np.ones_like(act(np.array(obs)[None], run_alpha, update_eps=update_eps))
            # r = np.array([0])
            # s_ = np.ones_like(np.array(obs)[None])
            # d = np.array([False])
            # s = obs[None]
            # a = np.array([action])
            # r = np.array([rew])
            # s_ = new_obs[None]
            # d = np.array([done])
            # if t % 100 == 0:
            #     for f in debug:
            #         print(f(s, a, r, s_, d))
            #     print('-------------')
            #
            #     # print([sess.run(v) for v in tf.global_variables('cvar_dqn/cvar_func')])
            #     # print([sess.run(v) for v in tf.global_variables('cvar_dqn/var_func')])

            # =================

            # Store transition in the replay buffer.
            replay_buffer.add(obs, action, rew, new_obs, float(done))
            obs = new_obs

            episode_rewards[-1] += rew
            if done:
                obs = env.reset()
                episode_rewards.append(0.0)
                reset = True
                if run_alpha is None:
                    alpha = np.random.random()

            if t > learning_starts and t % train_freq == 0:
                # Minimize the error in Bellman's equation on a batch sampled from replay buffer.

                obses_t, actions, rewards, obses_tp1, dones = replay_buffer.sample(batch_size)
                weights, batch_idxes = np.ones_like(rewards), None

                errors = train(obses_t, actions, rewards, obses_tp1, dones, weights)

            if t > learning_starts and t % target_network_update_freq == 0:
                # Update target network periodically.
                update_target()

            # Log results and periodically save the model
            mean_100ep_reward = round(float(np.mean(episode_rewards[-101:-1])), 1)
            num_episodes = len(episode_rewards)
            if done and print_freq is not None and len(episode_rewards) % print_freq == 0:
                logger.record_tabular("steps", t)
                logger.record_tabular("episodes", num_episodes)
                logger.record_tabular("mean 100 episode reward", mean_100ep_reward)
                logger.record_tabular("% time spent exploring", int(100 * exploration.value(t)))
                logger.record_tabular("(current alpha)", "%.2f" % alpha)
                logger.dump_tabular()

            # save and report best model
            if (checkpoint_freq is not None and t > learning_starts and
                    num_episodes > 100 and t % checkpoint_freq == 0):
                if saved_mean_reward is None or mean_100ep_reward > saved_mean_reward:
                    if print_freq is not None:
                        logger.log("Saving model due to mean reward increase: {} -> {}".format(
                                   saved_mean_reward, mean_100ep_reward))
                    U.save_state(model_file)
                    model_saved = True
                    saved_mean_reward = mean_100ep_reward

            # save periodically
            if periodic_save_freq is not None and periodic_save_path is not None and t > learning_starts:
                if t % periodic_save_freq == 0:
                    ActWrapper(act, act_params).save("{}-{}.pkl".format(periodic_save_path, int(t/periodic_save_freq)))

        if model_saved:
            if print_freq is not None:
                logger.log("Restored model with mean reward: {}".format(saved_mean_reward))
            U.load_state(model_file)

    return ActWrapper(act, act_params)
