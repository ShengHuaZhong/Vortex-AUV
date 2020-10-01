#!/usr/bin/env python
import rospy
import numpy as np
import math
from smach import State, StateMachine, Sequence
from smach_ros import SimpleActionState, MonitorState, IntrospectionServer
# action message
import actionlib
from actionlib_msgs.msg import GoalStatus
from geometry_msgs.msg import Pose, Point, Quaternion, PoseStamped, Wrench
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from nav_msgs.srv import GetPlan, GetMap
from tf.transformations import quaternion_from_euler, euler_from_quaternion
from visualization_msgs.msg import Marker, MarkerArray
#from autopilot.autopilot import AutopilotBackstepping, AutopilotPID
import actionlib
from vortex_msgs.msg import LosPathFollowingAction, LosPathFollowingGoal, LosPathFollowingResult, LosPathFollowingFeedback
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from autopilot.autopilot import AutopilotBackstepping, AutopilotPID
from PID.PIDregulator import PIDRegulator


class PidGlobalPlan():



    def __init__(self):
        
        # init node
        rospy.init_node('pid_global_plan', anonymous=False)

        # init variables
        self.x = 0
        self.y = 0
        self.z = 0

        self.roll = 0
        self.pitch = 0
        self.yaw = 0

        self.reached_goal = True
        self.desired_depth = -0.5

        self.path = []
        self.current_goal = PoseStamped()

        self.sphere_of_acceptance = 0.2
        self.vehicle_odom = Odometry()

        # pid regulators
        P_yaw = 25
        I_yaw = 0
        D_yaw = 10
        sat_yaw = 15
        self.PID_yaw = PIDRegulator(P_yaw,I_yaw,D_yaw,sat_yaw)  # p, i, d, sat

        P_side = 20
        I_side = 0
        D_side = 10
        sat_side = 5
        self.PID_side = PIDRegulator(P_side, I_side, D_side, sat_side)

        self.PID = AutopilotPID()

        self._feedback = LosPathFollowingFeedback()
        self._result = LosPathFollowingResult()

        rospy.sleep(1)

        # subscribers
        self.sub_pose = rospy.Subscriber('/odometry/filtered', Odometry, self.positionCallback, queue_size=1)
        self.sub_pose = rospy.Subscriber('/move_base_node/TrajectoryPlannerROS/global_plan', Path, self.globPlanCallback, queue_size=2)

        rospy.sleep(1)

        # publishers
        self.pub_thrust = rospy.Publisher('/manta/thruster_manager/input', Wrench, queue_size=1)
        self.marker_pub = rospy.Publisher('/pathplanning/closest_pt', Marker, queue_size=1)

        # action server
        rospy.sleep(1)

        self.move_base_client = actionlib.SimpleActionClient('move_base', MoveBaseAction)
        self.move_base_client.wait_for_server()

        self.action_server = actionlib.SimpleActionServer(name='pid_global_plan_server', ActionSpec=LosPathFollowingAction, auto_start=False)
        self.action_server.register_goal_callback(self.goalCB)
        self.action_server.register_preempt_callback(self.preemptCB)
        self.action_server.start()


        self.start_time = rospy.get_time()

        print("Rospy spin")


        
        print("Finished TaskManager")
    
    def goalCB(self):
        """
        Get a new goal from the action server
        Update the goal and pass to the move_base action server
        """
        self.reached_goal = False
        _goal = self.action_server.accept_new_goal()
        self.sphere_of_acceptance = _goal.sphereOfAcceptance
        self.desired_depth = _goal.desired_depth.z
        self.moveBaseClient(_goal)
        self.current_goal = _goal.next_waypoint

        PRINT_GOAL = True

        if PRINT_GOAL:
            print("sphere of accpetance: ", self.sphere_of_acceptance)
            print("desired depth: ", self.desired_depth)
            print("target x: ", _goal.next_waypoint.x)
            print("target y: ", _goal.next_waypoint.y)

    def moveBaseClient(self, _goal):
        """
        Client for sending goal to move_base action server
        """
        mb_goal = MoveBaseGoal()
        mb_goal.target_pose.header.frame_id = 'manta/odom'
        mb_goal.target_pose.header.stamp = rospy.Time.now()
        mb_goal.target_pose.pose.position.x = _goal.next_waypoint.x
        mb_goal.target_pose.pose.position.y =  _goal.next_waypoint.y
        mb_goal.target_pose.pose.orientation.w = 1.0
        self.move_base_client.send_goal(mb_goal)

    def globPlanCallback(self, msg):
        """
        Update path from move_base
        """
        self.path = msg.poses


    def withinSphereOfAcceptance(self):
        """
        Check if manta is within the spehere of acceptance of the goal
        """
        manta_pos = Point()
        manta_pos.x = self.x
        manta_pos.y = self.y

        distToGoal = self.distBetween2Positions(self.current_goal, manta_pos)
        return distToGoal < self.sphere_of_acceptance

    def distanceBetweenPoseAndSelf(self, pose):
        """
        Distance between a pose and manta
        """
        return np.sqrt((self.x-pose.position.x)**2 + abs(self.y - pose.position.y)**2)

    def distBetween2Poses(self, pose1, pose2):
        """
        Distance between 2 poses
        """
        return np.sqrt((pose1.position.x-pose2.position.x)**2 + abs(pose1.position.y-pose2.position.y)**2)

    def distBetween2Positions(self, pos1, pos2):
        """
        Distance between 2 positions
        """
        return np.sqrt((pos1.x-pos2.x)**2 + abs(pos1.y-pos2.y)**2)

    def shutdown(self):
        """
        Stop manta
        """
        rospy.loginfo("stopping the AUV...")
        rospy.sleep(10)

    def getCurvature(self, pose1, pose2, pose3):
        """
        Calculate curvature of path
        """
        
        side1 = self.distBetween2Poses(pose1,pose2)
        side2 = self.distBetween2Poses(pose2,pose3)
        side3 = self.distBetween2Poses(pose1, pose3)
        a = pose1.position
        b = pose2.position
        c = pose3.position
        
        area = (b.x-a.x)*(c.y-a.y) - (b.y-a.y)*(c.x-a.x)
        curvature = 4*area/(side1*side2*side3)
        
        return curvature

    def isValidRange(self, index, length_list):
        """
        Check for valid index in a list
        """
        return index >= 0 and index < length_list

    def statusActionGoal(self):
        """
        Check if within sphere of acceptance and if succeded, report to action server
        """
		# feedback
        self._feedback = LosPathFollowingFeedback()

        manta_pos = Point()
        manta_pos.x = self.x
        manta_pos.y = self.y


        self._feedback.distanceToGoal = self.distBetween2Positions(manta_pos, self.current_goal)
        self.action_server.publish_feedback(self._feedback)
		# succeeded
        if self.withinSphereOfAcceptance():
            print("Within sphere of acceptance")
            self._result.terminalSector = True
            self.action_server.set_succeeded(self._result, text="goal completed")
            self.reached_goal = True

    def preemptCB(self):
        """
        Cancel the action server
        """
        if self.action_server.is_preempt_requested():
            rospy.loginfo("Preempted requsted by global pid planner")
            self.action_server.set_preempted()

    def fixHeadingWrapping(self, err_yaw):
        """
        Fix error in yaw such that manta rotates in the closest direction
        Example: Instead of rotating 3/2 pi clockwise, rotate -1/2 pi counter clockwise
        """

        if err_yaw > math.pi:
            err_yaw = err_yaw - 2*math.pi

        if err_yaw < -math.pi:
            err_yaw = err_yaw + 2*math.pi

        return err_yaw

    def vectorMantaToPath(self, closest_pt):
        """
        Get the vector from manta to the closest point on the global path from move_base
        """
        vec_toward_path = np.array([closest_pt.x - self.x, closest_pt.y - self.y])
        rot_mat = np.array([[np.cos(self.yaw), -np.sin(self.yaw)],[np.sin(self.yaw), np.cos(self.yaw)]])
        rot_mat = rot_mat.transpose()

        dir_manta_frame = np.dot(rot_mat, vec_toward_path)

        return dir_manta_frame



    def controller(self):
        """
        Controller to follow the global plan
        Consists of 3 PID controllers: yaw PID, PID sideways eroor relative to path, and depth PID
        Functionality added so that manta turns in the right direction before following path
        """

        index,closest_pt = self.findClosestPointIndex()

        yaw = self.getYawFrom2Pos(self.path[index].pose.position, self.path[index+1].pose.position)

        # calculate curvature of path close to manta
        curvature = 0
        if self.isValidRange(index-1, len(self.path)) and self.isValidRange(index+1, len(self.path)): 
            curvature = self.getCurvature(self.path[index-5].pose, self.path[index].pose, self.path[index+5].pose)
        
        # yaw/angle error
        err_yaw = yaw - self.yaw
        err_yaw = self.fixHeadingWrapping(err_yaw)

        # get error sideways to path
        dir_manta_frame = self.vectorMantaToPath(closest_pt)
        err_side = dir_manta_frame[1]

        # regulate torque, extra torque if in a curved part of the path and facing correct direction
        torque_z = self.PID_yaw.regulate(err_yaw, rospy.get_time())
        if (err_yaw < 0.1):
            curvature_gain = min(curvature/10, 4)
            torque_z = torque_z + curvature_gain

        # regulate side force, sideways force only if facing the correct direction
        force_y = self.PID_side.regulate(err_side, rospy.get_time())
        force_y = np.sign(force_y)*(max(abs(force_y) - 5*abs(err_yaw), 0))

        # fix to make forward speed slow down if off track
        force_x = 10 # max force forward
        force_x = max(force_x - 10*abs(err_side) - 50*abs(err_yaw), 0)

        # depth controller
        tau_depth_hold = self.PID.depthController(self.desired_depth, self.z, rospy.get_time())

        # make wrench and publish
        wrench = Wrench()
        wrench.force.y = force_y
        wrench.force.x = force_x
        wrench.torque.z = torque_z
        wrench.force.z = tau_depth_hold
        self.pub_thrust.publish(wrench)

        # check status of goal
        self.statusActionGoal()

        # optional: print info for debugging
        print_info = False
        if print_info:
            print("contr")
            print("index: ", index, " closest_point:", closest_pt)
            print("Self yaw: ", self.yaw, "  Point Yaw: ", yaw)
            print("Error yaw: ", err_yaw)
            print("Error side: ", err_side, "   x_component: ", dir_manta_frame[0])
            print("Force x: ", force_x, " Force y: ", force_y, "Torque z: ", testWrench.torque.z )
            print("Curvature: ", curvature)
            print("tau depth hold:", tau_depth_hold)
            print("current depth: ", self.z)



    def getYawFrom2Pos(self, pos1, pos2):
        """
        Get the angle between two points
        """
        return np.arctan2((pos2.y - pos1.y),(pos2.x - pos1.x))


    
    def positionCallback(self, msg):
        """
        Callback on odometry/filtered at 20 Hz? 
        Runs controller 
        """
        self.vehicle_odom = msg
        self.time = msg.header.stamp.to_sec()
        global roll, pitch, yaw
        orientation_q = msg.pose.pose.orientation
        orientation_list = [orientation_q.x, orientation_q.y, orientation_q.z, orientation_q.w]
        (roll,pitch,yaw) = euler_from_quaternion(orientation_list)

        self.x = msg.pose.pose.position.x
        self.y = msg.pose.pose.position.y
        self.z = msg.pose.pose.position.z

        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw

        if (len(self.path) > 2):
            if not self.reached_goal:
                self.controller()
            else:
                pass
                #print("Goal reached")

    def findClosestPointIndex(self):
        """
        Find closest point in global plan
        """
        distArray = []
        for pose in self.path:
            distArray.append(abs(self.x-pose.pose.position.x)**2 + abs(self.y - pose.pose.position.y)**2)

        minIndex = np.argmin(distArray)
        closest_pt = self.path[minIndex].pose.position

        self.drawMarker(closest_pt)

        return minIndex, closest_pt
        
    def drawMarker(self, position):
        """
        Draw closest point in RVIZ
        """
        marker = Marker()
        marker.pose.position.x = position.x
        marker.pose.position.y = position.y
        marker.header.frame_id = "manta/odom"
        marker.type = marker.SPHERE
        marker.action = marker.ADD
        marker.scale.x = 0.1
        marker.scale.y = 0.1
        marker.scale.z = 0.1
        marker.color.a = 1.0
        marker.color.r = 1.0
        marker.color.g = 0.0
        marker.color.b = 0.0
        self.marker_pub.publish(marker)



if __name__ == '__main__':
	try:
		controllerObj = PidGlobalPlan()
		rospy.spin()
	except rospy.ROSInterruptException:
		pass
