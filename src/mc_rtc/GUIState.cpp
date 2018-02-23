#include <mc_rtc/GUIState.h>

namespace mc_rtc
{

namespace gui
{

Element::Element(const std::string & name)
: name_(name)
{
}

StateBuilder::StateBuilder()
{
  reset();
}

void StateBuilder::reset()
{
  elements_.elements.clear();
  elements_.sub.clear();
  state_ = mc_rtc::Configuration{};
  state_.add("DATA");
}

std::string StateBuilder::cat2str(const std::vector<std::string> & cat)
{
  std::string ret;
  for(size_t i = 0; i < cat.size(); ++i)
  {
    ret += cat[i];
    if(i != cat.size() - 1) { ret += "/"; }
  }
  return ret;
}

void StateBuilder::removeCategory(const std::vector<std::string> & category)
{
  if(category.size() == 0)
  {
    LOG_ERROR("You are not allowed to remove the root of the state")
    LOG_WARNING("Call clear() if this was your intent")
    return;
  }
  auto cat = getCategory(category, true);
  if(cat.first && cat.second.sub.count(category.back()))
  {
    cat.second.sub.erase(category.back());
  }
}

void StateBuilder::removeElement(const std::vector<std::string> & category, const std::string & name)
{
  bool found;
  std::reference_wrapper<Category> cat_(elements_);
  std::tie(found, cat_) = getCategory(category, false);
  Category & cat = cat_;
  if(found)
  {
    auto it = std::find_if(cat.elements.begin(), cat.elements.end(),
               [&name](const ElementStore & el)
               {
               return el().name() == name;
               });
    if(it != cat.elements.end())
    {
      cat.elements.erase(it);
    }
  }
}

const mc_rtc::Configuration & StateBuilder::update()
{
  update(elements_, state_);
  return state_;
}

void StateBuilder::update(Category & category,
                          mc_rtc::Configuration out)
{
  for(auto & e : category.elements)
  {
    auto & element = e();
    if(!out.has(element.name()))
    {
      out.add(element.name());
    }
    auto c = out(element.name());
    e.addData(element, c);
    auto g = c.add("GUI");
    e.addGUI(element, g);
  }
  for(auto & c : category.sub)
  {
    if(!out.has(c.first))
    {
      out.add(c.first);
    }
    update(c.second, out(c.first));
  }
}

bool StateBuilder::handleRequest(const std::vector<std::string> & category,
                                 const std::string & name,
                                 const mc_rtc::Configuration & data)
{
  bool found;
  std::reference_wrapper<Category> cat_(elements_);
  std::tie(found, cat_) = getCategory(category, false);
  if(!found)
  {
    LOG_ERROR("No category " << cat2str(category))
    return false;
  }
  Category & cat = cat_;
  auto it = std::find_if(cat.elements.begin(),
                         cat.elements.end(),
                         [&name](const ElementStore & el)
                         {
                         return el().name() == name;
                         });
  if(it == cat.elements.end())
  {
    LOG_ERROR("No element " << name << " in category " << cat2str(category))
    return false;
  }
  auto & el = *it;
  return el.handleRequest(el(), data);
}

mc_rtc::Configuration StateBuilder::data()
{
  return state_("DATA");
}

std::pair<bool, StateBuilder::Category&> StateBuilder::getCategory(const std::vector<std::string> & category, bool getParent)
{
  std::reference_wrapper<Category> cat_(elements_);
  size_t limit = category.size();
  if(getParent) { assert(limit > 0); limit -= 1; }
  for(size_t i = 0; i < limit; ++i)
  {
    const auto & c = category[i];
    Category & cat = cat_;
    if(!cat.sub.count(c)) { return {false, cat_}; }
    cat_ = cat.sub[c];
  }
  return {true, cat_};
}

StateBuilder::Category & StateBuilder::getCategory(const std::vector<std::string> & category)
{
  std::reference_wrapper<Category> cat_(elements_);
  for(const auto & c : category)
  {
    cat_ = cat_.get().sub[c];
  }
  return cat_;
}

const Element & StateBuilder::ElementStore::operator()() const
{
  return element();
}
Element & StateBuilder::ElementStore::operator()()
{
  return element();
}

} // namespace gui

} // namepsace mc_rtc
