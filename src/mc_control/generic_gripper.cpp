/*
 * Copyright 2015-2019 CNRS-UM LIRMM, CNRS-AIST JRL
 */

#include <mc_control/generic_gripper.h>
#include <mc_filter/utils/clamp.h>
#include <mc_rtc/constants.h>
#include <mc_rtc/gui.h>
#include <mc_rtc/logging.h>

#include <cmath>
#include <numeric>
#include <tinyxml2.h>

namespace mc_control
{

namespace
{

/* Returns the mimic joints stored in the URDF model */
std::vector<mc_rbdyn::Mimic> readMimic(const std::string & urdf)
{
  std::vector<mc_rbdyn::Mimic> res;

  tinyxml2::XMLDocument doc;
  doc.Parse(urdf.c_str());
  tinyxml2::XMLElement * robot = doc.FirstChildElement("robot");

  if(robot)
  {
    std::vector<tinyxml2::XMLElement *> joints;
    // Extract all joint elements from the document
    {
      tinyxml2::XMLElement * joint = robot->FirstChildElement("joint");
      while(joint)
      {
        joints.push_back(joint);
        joint = joint->NextSiblingElement("joint");
      }
    }

    for(auto & j : joints)
    {
      std::string jointName = j->Attribute("name");
      tinyxml2::XMLElement * mimicDom = j->FirstChildElement("mimic");
      if(mimicDom)
      {
        std::string j = mimicDom->Attribute("joint");
        double m = 1;
        mimicDom->QueryDoubleAttribute("multiplier", &m);
        double o = 0;
        mimicDom->QueryDoubleAttribute("offset", &o);

        res.push_back({jointName, j, m, o});
      }
    }
  }

  return res;
}

/* Returns all joints associated to a gripper's active joints */
std::vector<mc_rbdyn::Mimic> gripperMimics(const std::vector<std::string> & jointNames,
                                           const std::vector<mc_rbdyn::Mimic> & mimics)
{
  std::vector<mc_rbdyn::Mimic> res;

  for(const auto & gripperName : jointNames)
  {
    for(const auto & m : mimics)
    {
      if(m.joint == gripperName)
      {
        res.push_back(m);
      }
    }
  }

  return res;
}

} // namespace

Gripper::Gripper(const mc_rbdyn::Robot & robot,
                 const std::vector<std::string> & jointNames,
                 const std::string & robot_urdf,
                 const std::vector<double> & currentQ,
                 double timeStep,
                 bool reverseLimits)
: Gripper(robot, jointNames, gripperMimics(jointNames, readMimic(robot_urdf)), currentQ, timeStep, reverseLimits)
{
}

Gripper::Gripper(const mc_rbdyn::Robot & robot,
                 const std::vector<std::string> & jointNames,
                 const std::vector<mc_rbdyn::Mimic> & mimics,
                 const std::vector<double> & currentQ,
                 double timeStep,
                 bool reverseLimits)
: actualQ(currentQ)
{
  active_joints = jointNames;
  mult.resize(0);
  _q.resize(0);
  for(size_t i = 0; i < jointNames.size(); ++i)
  {
    const auto & name = jointNames[i];
    if(robot.hasJoint(name))
    {
      unsigned int jointIndex = robot.jointIndexByName(name);
      if(!reverseLimits)
      {
        closeP.push_back(robot.ql()[jointIndex][0]);
        openP.push_back(robot.qu()[jointIndex][0]);
      }
      else
      {
        closeP.push_back(robot.qu()[jointIndex][0]);
        openP.push_back(robot.ql()[jointIndex][0]);
      }
      vmax.push_back(std::min(std::abs(robot.vl()[jointIndex][0]), robot.vu()[jointIndex][0]));
      _q.push_back(currentQ[i]);
    }
    else
    {
      LOG_ERROR("Gripper active joint " << name << " is not part of the loaded robot, limits are unknown")
      closeP.push_back(-0.01);
      openP.push_back(0.01);
      vmax.push_back(0);
      _q.push_back(0);
    }
    mult.push_back({i, 1.0});
    offset.push_back(0.0);
  }
  names = jointNames;
  auto getActiveIdx = [this](const std::string & joint) {
    for(size_t i = 0; i < active_joints.size(); ++i)
    {
      if(active_joints[i] == joint)
      {
        return i;
      }
    }
    LOG_ERROR_AND_THROW(std::runtime_error, "Trying to mimic non existant joint: " << joint)
  };
  for(const auto & m : mimics)
  {
    names.push_back(m.name);
    auto jIndex = getActiveIdx(m.joint);
    _q.push_back(m.multiplier * _q[jIndex] + m.offset);
    mult.push_back({jIndex, m.multiplier});
    offset.push_back(m.offset);
  }

  percentOpen.resize(currentQ.size());
  overCommandLimit.resize(currentQ.size());
  overCommandLimitIter.resize(currentQ.size());
  setCurrentQ(currentQ);
  this->timeStep = timeStep;

  targetQIn = {};
  targetQ = nullptr;

  reversed = reverseLimits;
}

void Gripper::resetDefaults()
{
  config_.percentVMax = DEFAULT_PERCENT_VMAX;
  config_.actualCommandDiffTrigger = DEFAULT_ACTUAL_COMMAND_DIFF_TRIGGER;
  config_.overCommandLimitIterN = DEFAULT_OVER_COMMAND_LIMIT_ITER_N;
  config_.releaseSafetyOffset = DEFAULT_RELEASE_OFFSET;
}

void Gripper::saveConfig()
{
  savedConfig_ = config_;
}

void Gripper::restoreConfig()
{
  config_ = savedConfig_;
}

void Gripper::setCurrentQ(const std::vector<double> & currentQ)
{
  for(size_t i = 0; i < percentOpen.size(); ++i)
  {
    percentOpen[i] = (currentQ[i] - closeP[i]) / (openP[i] - closeP[i]);
  }
}

void Gripper::setTargetQ(const std::vector<double> & targetQ)
{
  targetQIn = targetQ;
  this->targetQ = &targetQIn;
}

void Gripper::setTargetOpening(double targetOpening)
{
  targetOpening = mc_filter::utils::clamp(targetOpening, 0, 1);
  auto cur = curPosition();
  std::vector<double> targetQin(cur.size());
  for(size_t i = 0; i < targetQin.size(); ++i)
  {
    targetQin[i] = cur[i] + (targetOpening - percentOpen[i]) * (openP[i] - closeP[i]);
  }
  setTargetQ(targetQin);
}

void Gripper::percentVMAX(double percent)
{
  config_.percentVMax = mc_filter::utils::clamp(percent, 0, 1);
}

double Gripper::percentVMAX() const
{
  return config_.percentVMax;
}

std::vector<double> Gripper::curPosition() const
{
  std::vector<double> res(percentOpen.size());
  for(size_t i = 0; i < res.size(); ++i)
  {
    res[i] = closeP[i] + (openP[i] - closeP[i]) * percentOpen[i];
  }
  return res;
}

void Gripper::setActualQ(const std::vector<double> & q)
{
  actualQ = q;
  auto currentQ = curPosition();
  for(size_t i = 0; i < actualQ.size(); ++i)
  {
    if(std::abs(actualQ[i] - currentQ[i]) > config_.actualCommandDiffTrigger)
    {
      overCommandLimitIter[i]++;
      if(overCommandLimitIter[i] == config_.overCommandLimitIterN)
      {
        LOG_WARNING("Gripper safety triggered on " << names[i])
        overCommandLimit[i] = true;
        if(reversed)
        {
          actualQ[i] = actualQ[i] + config_.releaseSafetyOffset;
        }
        else
        {
          actualQ[i] = actualQ[i] - config_.releaseSafetyOffset;
        }
        setTargetQ(actualQ);
      }
    }
    else
    {
      overCommandLimitIter[i] = 0;
      overCommandLimit[i] = false;
    }
  }
}

const std::vector<double> & Gripper::q()
{
  if(targetQ)
  {
    auto cur = curPosition();
    bool reached = true;
    for(size_t i = 0; i < cur.size(); ++i)
    {
      bool i_reached = std::abs(cur[i] - targetQIn[i]) < 0.001;
      if(!i_reached)
      {
        if(targetQIn[i] > cur[i])
        {
          percentOpen[i] +=
              std::min(vmax[i] * config_.percentVMax * timeStep, targetQIn[i] - cur[i]) / (openP[i] - closeP[i]);
        }
        else
        {
          percentOpen[i] +=
              std::max(-vmax[i] * config_.percentVMax * timeStep, targetQIn[i] - cur[i]) / (openP[i] - closeP[i]);
        }
      }
      reached = reached && i_reached;
    }
    if(reached)
    {
      targetQ = nullptr;
    }
  }
  auto cur = curPosition();
  for(size_t i = 0; i < active_joints.size(); ++i)
  {
    _q[i] = cur[i];
  }
  for(size_t i = active_joints.size(); i < names.size(); ++i)
  {
    _q[i] = mult[i].second * _q[mult[i].first] + offset[i];
  }
  return _q;
}

double Gripper::opening() const
{
  return std::accumulate(percentOpen.begin(), percentOpen.end(), 0.0) / static_cast<double>(percentOpen.size());
}

bool Gripper::complete() const
{
  return targetQ == nullptr;
}

void Gripper::addToGUI(mc_rtc::gui::StateBuilder & gui, std::vector<std::string> category)
{
  using namespace mc_rtc::gui;
  std::vector<std::string> cat = category;
  cat.push_back(name);
  gui.addElement(cat, Button("Open", [this]() { setTargetOpening(1); }),
                 Button("Close", [this]() { setTargetOpening(0); }),
                 NumberSlider("Opening percentage", [this]() { return opening(); },
                              [this](double op) { setTargetOpening(op); }, 0, 1),
                 NumberSlider("Percentage VMAX", [this]() { return percentVMAX(); },
                              [this](double op) { percentVMAX(op); }, 0, 1));
  std::vector<std::string> cat_safety = cat;
  cat_safety.push_back("Safety");
  gui.addElement(cat_safety,
                 NumberInput("Actual command diff threshold [deg]",
                             [this]() { return mc_rtc::constants::toDeg(actualCommandDiffTrigger()); },
                             [this](double deg) { actualCommandDiffTrigger(mc_rtc::constants::toRad(deg)); }),
                 NumberInput("Over command limiter iterations", [this]() -> double { return overCommandLimitIterN(); },
                             [this](double N) { overCommandLimitIterN(static_cast<unsigned int>(N)); }),
                 NumberInput("Release offset [deg]",
                             [this]() { return mc_rtc::constants::toDeg(releaseSafetyOffset()); },
                             [this](double deg) { releaseSafetyOffset(mc_rtc::constants::toRad(deg)); }));
}

void Gripper::removeFromGUI(mc_rtc::gui::StateBuilder & gui, std::vector<std::string> category)
{
  std::vector<std::string> cat = category;
  cat.push_back(name);
  gui.removeCategory(cat);
}

} // namespace mc_control
