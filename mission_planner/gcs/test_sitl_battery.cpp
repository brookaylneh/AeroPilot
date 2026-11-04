#include <AP_gtest.h>
#include <SITL/SIM_AirSim.h>

const AP_HAL::HAL &hal = AP_HAL::get_HAL();
static SITL::SIM simulator;

class BatteryCopter final : public SITL::AirSim {
public:
    BatteryCopter() : AirSim("airsim-copter") {}
    using AirSim::update_battery;
    using Aircraft::battery_current;
    using Aircraft::battery_voltage;
    using Aircraft::motor_mask;
};

class AirSimBattery : public testing::Test {
protected:
    void SetUp() override
    {
        simulator.batt_capacity_ah.set(3.3f);
        simulator.batt_voltage.set(12.6f);
        hal.scheduler->stop_clock(1);
    }
};

TEST_F(AirSimBattery, MotorChannelsAndDisarmedBattery)
{
    auto aircraft = new BatteryCopter;
    sitl_input input{};
    aircraft->update_battery(input);
    EXPECT_EQ(aircraft->motor_mask, 15U);
    EXPECT_FLOAT_EQ(aircraft->battery_current, 0.0f);
    EXPECT_FLOAT_EQ(aircraft->battery_voltage, 12.6f);
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 1500;
    }
    input.servos[4] = 2000;
    aircraft->update_battery(input);
    EXPECT_FLOAT_EQ(aircraft->battery_current, 25.0f);
    input.servos[0] = 1000;
    aircraft->update_battery(input);
    EXPECT_FLOAT_EQ(aircraft->battery_current, 18.75f);
    delete aircraft;
}

TEST_F(AirSimBattery, DischargeReachesLowVoltageAndDoesNotRecharge)
{
    auto aircraft = new BatteryCopter;
    sitl_input input{};
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 2000;
    }
    aircraft->update_battery(input);
    float seconds = 0;
    for (uint64_t now = 10001; aircraft->battery_voltage >= 10.5f && now <= 240000001; now += 10000) {
        hal.scheduler->stop_clock(now);
        aircraft->update_battery(input);
        seconds = (now - 1) * 1.0e-6f;
    }
    EXPECT_FLOAT_EQ(aircraft->battery_current, 50.0f);
    EXPECT_LT(aircraft->battery_voltage, 10.5f);
    EXPECT_GT(seconds, 0.0f);
    EXPECT_LT(seconds, 3.3f / 50.0f * 3600);
    printf("battery_model full_load_a=50 capacity_ah=3.3 low_voltage_v=10.5 crossing_s=%.2f consumed_ah=%.6f voltage_v=%.6f\n",
           seconds, seconds * 50 / 3600, aircraft->battery_voltage);
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 1000;
    }
    for (uint64_t i = 1; i <= 1000; i++) {
        hal.scheduler->stop_clock(static_cast<uint64_t>(seconds * 1000000) + 1 + i * 10000);
        aircraft->update_battery(input);
    }
    EXPECT_FLOAT_EQ(aircraft->battery_current, 0.0f);
    EXPECT_LT(aircraft->battery_voltage, 12.0f);
    delete aircraft;
}

TEST_F(AirSimBattery, ParameterChangeReinitialisesStateOfCharge)
{
    auto aircraft = new BatteryCopter;
    sitl_input input{};
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 2000;
    }
    aircraft->update_battery(input);
    for (uint64_t now = 10001; now <= 120000001; now += 10000) {
        hal.scheduler->stop_clock(now);
        aircraft->update_battery(input);
    }
    const float drained = aircraft->battery_voltage;
    EXPECT_LT(drained, 12.0f);
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 1000;
    }
    // Writing the same SIM_BATT_VOLTAGE changes nothing; a different value re-initialises.
    simulator.batt_voltage.set(12.6f);
    hal.scheduler->stop_clock(120010001);
    aircraft->update_battery(input);
    EXPECT_LT(aircraft->battery_voltage, 12.3f);
    simulator.batt_voltage.set(12.61f);
    hal.scheduler->stop_clock(120020001);
    aircraft->update_battery(input);
    EXPECT_GT(aircraft->battery_voltage, 12.5f);
    simulator.batt_voltage.set(12.6f);
    hal.scheduler->stop_clock(120030001);
    aircraft->update_battery(input);
    EXPECT_FLOAT_EQ(aircraft->battery_voltage, 12.6f);
    printf("battery_model reset drained_v=%.6f refilled_v=%.6f\n", drained, aircraft->battery_voltage);
    delete aircraft;
}

TEST_F(AirSimBattery, UnlimitedCapacityFails)
{
    simulator.batt_capacity_ah.set(0);
    EXPECT_DEATH({
        auto aircraft = new BatteryCopter;
        aircraft->update_battery(sitl_input{});
    }, "AirSim requires positive");
}

TEST_F(AirSimBattery, FiftyAhProfileSupportsThirtyMinutesBeforeReserve)
{
    simulator.batt_capacity_ah.set(50);
    auto aircraft = new BatteryCopter;
    sitl_input input{};
    for (unsigned i = 0; i < 4; i++) {
        input.servos[i] = 2000;
    }
    aircraft->update_battery(input);
    float voltage_at_half_charge = 0;
    float seconds = 0;
    for (uint64_t now = 10001; aircraft->battery_voltage >= 10.5f && now <= 3600000001; now += 10000) {
        hal.scheduler->stop_clock(now);
        aircraft->update_battery(input);
        seconds = (now - 1) * 1.0e-6f;
        if (now == 1800000001) {
            voltage_at_half_charge = aircraft->battery_voltage;
        }
    }
    EXPECT_GT(voltage_at_half_charge, 10.5f);
    EXPECT_LT(voltage_at_half_charge, 10.7f);
    EXPECT_LT(aircraft->battery_voltage, 10.5f);
    EXPECT_GT(seconds, 1800.0f);
    EXPECT_LT(seconds, 3600.0f);
    printf("battery_model full_load_a=50 capacity_ah=50 half_charge_s=1800 half_charge_voltage_v=%.6f low_voltage_v=10.5 crossing_s=%.2f\n",
           voltage_at_half_charge, seconds);
    delete aircraft;
}

AP_GTEST_PANIC()
AP_GTEST_MAIN()
